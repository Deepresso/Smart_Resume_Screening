import os
from functools import wraps
import csv
import io
import random
import re
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, redirect, url_for, request, flash, Response, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
from models import db, User, JobPosting, Application
from screening import extract_text, compute_scores, keyword_breakdown, fuzzy_breakdown

MY_PHONE_RE = re.compile(r'^(\+?60|0)1[0-9]\d{7,8}$')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

_db_url = os.environ.get('DATABASE_URL', 'sqlite:///smart_resume.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}

app.config['MAIL_SERVER']   = 'smtp.gmail.com'
app.config['MAIL_PORT']     = 587
app.config['MAIL_USE_TLS']  = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

mail = Mail(app)
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {'pdf', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

db.init_app(app)
bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def hr_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role != 'hr':
            return redirect(url_for('applicant_dashboard'))
        return f(*args, **kwargs)
    return decorated

def applicant_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role != 'applicant':
            return redirect(url_for('hr_dashboard'))
        return f(*args, **kwargs)
    return decorated

with app.app_context():
    db.create_all()
    from sqlalchemy import text, inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    dialect = db.engine.dialect.name  # 'sqlite' or 'postgresql'
    bool_true = 'TRUE' if dialect == 'postgresql' else '1'

    if inspector.has_table('applications'):
        existing = [c['name'] for c in inspector.get_columns('applications')]
        if 'semantic_score' not in existing:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE applications ADD COLUMN semantic_score FLOAT DEFAULT 0.0'))
                conn.commit()

    if inspector.has_table('users'):
        existing = [c['name'] for c in inspector.get_columns('users')]
        new_cols = {
            'phone':       'VARCHAR(20)',
            'address1':    'VARCHAR(200)',
            'address2':    'VARCHAR(200)',
            'postcode':    'VARCHAR(10)',
            'state':       'VARCHAR(50)',
            'verify_code': 'VARCHAR(6)',
            'is_verified': f'BOOLEAN DEFAULT {bool_true}',
        }
        with db.engine.connect() as conn:
            for col, col_type in new_cols.items():
                if col not in existing:
                    conn.execute(text(f'ALTER TABLE users ADD COLUMN {col} {col_type}'))
            conn.commit()

    # Seed default HR account if none exists
    if not User.query.filter_by(role='hr').first():
        hashed = bcrypt.generate_password_hash('admin123').decode('utf-8')
        hr = User(name='HR Admin', email='admin@smartresume.com', password=hashed,
                  role='hr', is_verified=True)
        db.session.add(hr)
        db.session.commit()

# Auth
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            if not user.is_verified:
                session['pending_verify_id'] = user.id
                flash('Please verify your email before logging in.')
                return redirect(url_for('verify'))
            login_user(user)
            return redirect(url_for('hr_dashboard') if user.role == 'hr' else url_for('applicant_dashboard'))
        flash('Invalid email or password.')
    return render_template('auth/login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name  = request.form.get('last_name', '').strip()
        email      = request.form.get('email', '').strip()
        phone      = request.form.get('phone', '').strip()
        address1   = request.form.get('address1', '').strip()
        address2   = request.form.get('address2', '').strip()
        postcode   = request.form.get('postcode', '').strip()
        state      = request.form.get('state', '').strip()
        password   = request.form.get('password', '')
        confirm    = request.form.get('confirm_password', '')
        if not all([first_name, last_name, email, phone, address1, postcode, state, password]):
            flash('Please fill in all required fields.')
        elif not MY_PHONE_RE.match(phone):
            flash('Invalid phone number. Use Malaysian format e.g. 0123456789 or +60123456789.')
        elif password != confirm:
            flash('Passwords do not match.')
        elif User.query.filter_by(email=email).first():
            flash('An account with this email already exists.')
        elif User.query.filter_by(phone=phone).first():
            flash('An account with this phone number already exists.')
        else:
            code   = str(random.randint(1000, 9999))
            hashed = bcrypt.generate_password_hash(password).decode('utf-8')
            user   = User(name=f'{first_name} {last_name}', email=email, password=hashed,
                          role='applicant', phone=phone, address1=address1, address2=address2,
                          postcode=postcode, state=state, is_verified=False, verify_code=code)
            db.session.add(user)
            db.session.commit()
            try:
                msg = Message('Your SmartResume Verification Code',
                              sender=f'SmartResume <{app.config["MAIL_USERNAME"]}>', recipients=[email])
                msg.body = f'Hi {first_name},\n\nYour SmartResume verification code is: {code}\n\nEnter this code when you log in to activate your account.\n\nSmartResume - UOW Malaysia KDU'
                msg.html = f"""
                <div style="font-family:sans-serif;max-width:420px;margin:0 auto;">
                    <h2 style="color:#1e293b;">Verify your account</h2>
                    <p>Hi {first_name},</p>
                    <p>Your SmartResume verification code is:</p>
                    <div style="background:#fff7ed;border-radius:12px;padding:24px;text-align:center;margin:20px 0;">
                        <span style="font-size:42px;font-weight:900;letter-spacing:10px;color:#ea580c;">{code}</span>
                    </div>
                    <p>Enter this code on the verification page when you log in to activate your account.</p>
                    <p style="color:#64748b;font-size:12px;">SmartResume &mdash; UOW Malaysia KDU</p>
                </div>"""
                mail.send(msg)
            except Exception as e:
                app.logger.error(f'Mail send failed: {e}')
            session['registered_email'] = email
            return redirect(url_for('register_success'))
    return render_template('auth/register.html')

@app.route('/register/success')
def register_success():
    email = session.pop('registered_email', None)
    if not email:
        return redirect(url_for('register'))
    return render_template('auth/register_success.html', email=email)

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    user_id = session.get('pending_verify_id')
    if not user_id:
        return redirect(url_for('login'))
    user = User.query.get(user_id)
    if not user:
        return redirect(url_for('login'))
    if request.method == 'POST':
        entered = request.form.get('code', '').strip()
        if entered == user.verify_code:
            user.is_verified = True
            user.verify_code  = None
            db.session.commit()
            session.pop('pending_verify_id', None)
            login_user(user)
            flash('Email verified! Welcome to SmartResume.')
            return redirect(url_for('applicant_dashboard'))
        else:
            flash('Incorrect code. Please try again.')
    return render_template('auth/verify.html', email=user.email)

@app.route('/resend-code', methods=['POST'])
def resend_code():
    user_id = session.get('pending_verify_id')
    if not user_id:
        return redirect(url_for('login'))
    user = User.query.get(user_id)
    if not user:
        return redirect(url_for('login'))
    code = str(random.randint(1000, 9999))
    user.verify_code = code
    db.session.commit()
    try:
        msg = Message('Your SmartResume Verification Code',
                      sender=app.config['MAIL_USERNAME'], recipients=[user.email])
        msg.html = f"""
        <div style="font-family:sans-serif;max-width:420px;margin:0 auto;">
            <h2 style="color:#1e293b;">New Verification Code</h2>
            <p>Your new SmartResume verification code is:</p>
            <div style="background:#fff7ed;border-radius:12px;padding:24px;text-align:center;margin:20px 0;">
                <span style="font-size:42px;font-weight:900;letter-spacing:10px;color:#ea580c;">{code}</span>
            </div>
            <p style="color:#64748b;font-size:12px;">SmartResume &mdash; UOW Malaysia KDU</p>
        </div>"""
        mail.send(msg)
        flash('A new code has been sent to your email.')
    except Exception:
        flash('Failed to send email. Please try again.')
    return redirect(url_for('verify'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# HR Routes
@app.route('/hr/dashboard')
@login_required
@hr_required
def hr_dashboard():
    jobs = JobPosting.query.filter_by(created_by=current_user.id).order_by(JobPosting.created_at.desc()).all()
    total_apps = Application.query.join(JobPosting).filter(JobPosting.created_by == current_user.id).count()
    shortlisted = Application.query.join(JobPosting).filter(JobPosting.created_by == current_user.id, Application.status == 'shortlisted').count()
    pending = Application.query.join(JobPosting).filter(JobPosting.created_by == current_user.id, Application.status == 'submitted').count()
    top_apps = Application.query.join(JobPosting).filter(JobPosting.created_by == current_user.id).order_by(Application.composite_score.desc()).limit(5).all()
    return render_template('hr/dashboard.html', jobs=jobs, total_apps=total_apps, shortlisted=shortlisted, pending=pending, top_apps=top_apps)

@app.route('/hr/jobs')
@login_required
@hr_required
def hr_jobs():
    jobs = JobPosting.query.filter_by(created_by=current_user.id).order_by(JobPosting.created_at.desc()).all()
    active_count = sum(1 for j in jobs if j.is_active)
    return render_template('hr/job_postings.html', jobs=jobs, active_count=active_count, closed_count=len(jobs) - active_count)

@app.route('/hr/jobs/new', methods=['GET', 'POST'])
@login_required
@hr_required
def hr_new_job():
    if request.method == 'POST':
        title       = request.form.get('title', '').strip()
        company     = request.form.get('company', '').strip()
        location    = request.form.get('location', '').strip()
        description = request.form.get('description', '').strip()
        keywords    = request.form.get('keywords', '').strip()
        if not title or not description:
            flash('Job title and description are required.')
        else:
            job = JobPosting(title=title, company=company, location=location,
                             description=description, keywords=keywords,
                             created_by=current_user.id)
            db.session.add(job)
            db.session.commit()
            return redirect(url_for('hr_jobs'))
    return render_template('hr/new_job_posting.html')

@app.route('/hr/jobs/<int:job_id>/edit', methods=['GET', 'POST'])
@login_required
@hr_required
def hr_edit_job(job_id):
    job = JobPosting.query.get_or_404(job_id)
    if request.method == 'POST':
        title       = request.form.get('title', '').strip()
        company     = request.form.get('company', '').strip()
        location    = request.form.get('location', '').strip()
        description = request.form.get('description', '').strip()
        keywords    = request.form.get('keywords', '').strip()
        if not title or not description:
            flash('Job title and description are required.')
        else:
            job.title       = title
            job.company     = company
            job.location    = location
            job.description = description
            job.keywords    = keywords
            db.session.commit()
            return redirect(url_for('hr_jobs'))
    return render_template('hr/edit_job_posting.html', job=job)

@app.route('/hr/jobs/<int:job_id>/toggle', methods=['POST'])
@login_required
@hr_required
def hr_toggle_job(job_id):
    job = JobPosting.query.get_or_404(job_id)
    job.is_active = not job.is_active
    db.session.commit()
    return redirect(url_for('hr_jobs'))

@app.route('/hr/jobs/<int:job_id>/delete', methods=['POST'])
@login_required
@hr_required
def hr_delete_job(job_id):
    job = JobPosting.query.get_or_404(job_id)
    Application.query.filter_by(job_id=job_id).delete()
    db.session.delete(job)
    db.session.commit()
    return redirect(url_for('hr_jobs'))

@app.route('/hr/screening')
@login_required
@hr_required
def hr_screening_index():
    jobs = JobPosting.query.filter_by(created_by=current_user.id).order_by(JobPosting.created_at.desc()).all()
    return render_template('hr/screening_index.html', jobs=jobs)

@app.route('/hr/jobs/<int:job_id>/results')
@login_required
@hr_required
def hr_screening_results(job_id):
    job = JobPosting.query.get_or_404(job_id)
    applications = Application.query.filter_by(job_id=job_id).order_by(Application.composite_score.desc()).all()
    return render_template('hr/screening_results.html', job=job, applications=applications)

@app.route('/hr/jobs/<int:job_id>/export-csv')
@login_required
@hr_required
def hr_export_csv(job_id):
    job = JobPosting.query.get_or_404(job_id)
    applications = Application.query.filter_by(job_id=job_id).order_by(Application.composite_score.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Rank', 'Name', 'Email', 'Composite Score (%)', 'Keyword Score (%)',
                     'Fuzzy Score (%)', 'Text Similarity (%)', 'BERT Semantic (%)', 'Status', 'Applied Date'])
    for i, appl in enumerate(applications, 1):
        writer.writerow([i, appl.applicant.name, appl.applicant.email,
                         appl.composite_score, appl.keyword_score, appl.fuzzy_score,
                         appl.similarity_score, appl.semantic_score, appl.status,
                         appl.applied_at.strftime('%Y-%m-%d')])
    safe_title = ''.join(c if c.isalnum() else '_' for c in job.title)
    filename = f"screening_{safe_title}_{job_id}.csv"
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment;filename={filename}'})

@app.route('/hr/applications/<int:app_id>/shortlist', methods=['POST'])
@login_required
@hr_required
def hr_shortlist_application(app_id):
    appl = Application.query.get_or_404(app_id)
    appl.status = 'shortlisted'
    db.session.commit()
    return redirect(request.referrer or url_for('hr_candidates'))

@app.route('/hr/applications/<int:app_id>/reject', methods=['POST'])
@login_required
@hr_required
def hr_reject_application(app_id):
    appl = Application.query.get_or_404(app_id)
    appl.status = 'rejected'
    db.session.commit()
    return redirect(request.referrer or url_for('hr_candidates'))

@app.route('/hr/candidates/<int:candidate_id>')
@login_required
@hr_required
def hr_candidate_detail(candidate_id):
    appl = Application.query.get_or_404(candidate_id)
    all_apps = Application.query.filter_by(job_id=appl.job_id).order_by(Application.composite_score.desc()).all()
    rank = next((i + 1 for i, a in enumerate(all_apps) if a.id == appl.id), 1)
    total = len(all_apps)
    kw_list = [k.strip() for k in appl.job.keywords.split(',') if k.strip()] if appl.job.keywords else []
    kw_detail = keyword_breakdown(appl.resume_text or '', kw_list)
    fz_detail = fuzzy_breakdown(appl.resume_text or '', kw_list)
    return render_template('hr/candidate_detail.html', appl=appl, rank=rank, total=total,
                           kw_detail=kw_detail, fz_detail=fz_detail)

@app.route('/hr/candidates')
@login_required
@hr_required
def hr_candidates():
    applications = Application.query.join(JobPosting).filter(JobPosting.created_by == current_user.id).order_by(Application.composite_score.desc()).all()
    return render_template('hr/candidates.html', applications=applications)

@app.route('/hr/users')
@login_required
@hr_required
def hr_user_management():
    users = User.query.order_by(User.role, User.id).all()
    return render_template('hr/user_management.html', users=users)

@app.route('/hr/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@hr_required
def hr_edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        user.name        = request.form.get('name', '').strip() or user.name
        user.email       = request.form.get('email', '').strip() or user.email
        user.phone       = request.form.get('phone', '').strip()
        user.address1    = request.form.get('address1', '').strip()
        user.address2    = request.form.get('address2', '').strip()
        user.postcode    = request.form.get('postcode', '').strip()
        user.state       = request.form.get('state', '').strip()
        user.is_verified = request.form.get('is_verified') == '1'
        db.session.commit()
        flash('User updated successfully.')
        return redirect(url_for('hr_user_management'))
    return render_template('hr/edit_user.html', user=user)

@app.route('/hr/users/<int:user_id>/delete', methods=['POST'])
@login_required
@hr_required
def hr_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.role == 'hr':
        flash('HR accounts cannot be deleted.')
        return redirect(url_for('hr_user_management'))
    Application.query.filter_by(user_id=user_id).delete()
    db.session.delete(user)
    db.session.commit()
    flash('User deleted.')
    return redirect(url_for('hr_user_management'))

@app.route('/hr/settings', methods=['GET', 'POST'])
@login_required
@hr_required
def hr_settings():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        new_password = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not name or not email:
            flash('Name and email are required.')
        elif email != current_user.email and User.query.filter_by(email=email).first():
            flash('That email is already in use.')
        elif new_password and new_password != confirm:
            flash('Passwords do not match.')
        else:
            current_user.name = name
            current_user.email = email
            if new_password:
                current_user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
            db.session.commit()
            flash('Settings saved successfully.')
    return render_template('hr/settings.html')

# Applicant Routes
@app.route('/applicant/dashboard')
@login_required
@applicant_required
def applicant_dashboard():
    jobs = JobPosting.query.filter_by(is_active=True).order_by(JobPosting.created_at.desc()).limit(4).all()
    applications = Application.query.filter_by(user_id=current_user.id).order_by(Application.applied_at.desc()).limit(3).all()
    return render_template('applicant/dashboard.html', jobs=jobs, applications=applications)

@app.route('/applicant/jobs')
@login_required
@applicant_required
def applicant_jobs():
    jobs = JobPosting.query.filter_by(is_active=True).order_by(JobPosting.created_at.desc()).all()
    return render_template('applicant/browse_jobs.html', jobs=jobs)

@app.route('/applicant/jobs/<int:job_id>/apply', methods=['GET', 'POST'])
@login_required
@applicant_required
def applicant_apply(job_id):
    job = JobPosting.query.get_or_404(job_id)
    if request.method == 'POST':
        if Application.query.filter_by(user_id=current_user.id, job_id=job_id).first():
            flash('You have already applied for this job.')
            return render_template('applicant/apply_job.html', job=job)
        file = request.files.get('resume')
        if not file or file.filename == '':
            flash('Please upload your resume.')
            return render_template('applicant/apply_job.html', job=job)
        if not allowed_file(file.filename):
            flash('Only PDF and DOCX files are accepted.')
            return render_template('applicant/apply_job.html', job=job)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        filename = secure_filename(f"{current_user.id}_{job_id}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        resume_text = extract_text(filepath)
        scores = compute_scores(resume_text, job.description, job.keywords or '')
        application = Application(
            user_id=current_user.id,
            job_id=job_id,
            resume_path=filename,
            resume_text=resume_text,
            cover_letter=request.form.get('cover_letter', ''),
            keyword_score=scores['keyword_score'],
            fuzzy_score=scores['fuzzy_score'],
            similarity_score=scores['similarity_score'],
            semantic_score=scores['semantic_score'],
            composite_score=scores['composite_score'],
            status='submitted'
        )
        db.session.add(application)
        db.session.commit()
        return redirect(url_for('applicant_applications'))
    return render_template('applicant/apply_job.html', job=job)

@app.route('/applicant/applications')
@login_required
@applicant_required
def applicant_applications():
    applications = Application.query.filter_by(user_id=current_user.id).order_by(Application.applied_at.desc()).all()
    return render_template('applicant/my_applications.html', applications=applications)

@app.route('/applicant/applications/<int:app_id>')
@login_required
@applicant_required
def applicant_application_detail(app_id):
    appl = Application.query.filter_by(id=app_id, user_id=current_user.id).first_or_404()
    all_apps = Application.query.filter_by(job_id=appl.job_id).order_by(Application.composite_score.desc()).all()
    rank = next((i + 1 for i, a in enumerate(all_apps) if a.id == appl.id), 1)
    total = len(all_apps)
    return render_template('applicant/application_detail.html', appl=appl, rank=rank, total=total)

@app.route('/applicant/settings', methods=['GET', 'POST'])
@login_required
@applicant_required
def applicant_settings():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        new_password = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not name or not email:
            flash('Name and email are required.')
        elif email != current_user.email and User.query.filter_by(email=email).first():
            flash('That email is already in use.')
        elif new_password and new_password != confirm:
            flash('Passwords do not match.')
        else:
            current_user.name = name
            current_user.email = email
            if new_password:
                current_user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
            db.session.commit()
            flash('Settings saved successfully.')
    return render_template('applicant/settings.html')

@app.route('/applicant/upload')
@login_required
@applicant_required
def applicant_upload():
    return render_template('applicant/upload_resume.html')

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)
