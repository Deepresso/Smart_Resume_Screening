import os
from functools import wraps
import csv
import io
import random
import re
import threading
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, redirect, url_for, request, flash, Response, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail as SGMail
from werkzeug.utils import secure_filename
from models import db, User, JobPosting, Application, Notification
from screening import extract_text, compute_scores, keyword_breakdown, fuzzy_breakdown, semantic_score

MY_PHONE_RE = re.compile(r'^(\+?60|0)1[0-9]\d{7,8}$')

app = Flask(__name__)
app.config['SECRET_KEY'] = 'smart-resume-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///smart_resume.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

SENDGRID_API_KEY  = os.environ.get('SENDGRID_API_KEY')
MAIL_SENDER_EMAIL = os.environ.get('MAIL_USERNAME', 'resumatch.fyp@gmail.com')
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

def send_email_async(to_email, subject, html_content, plain_content):
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        message = SGMail(
            from_email=f'Resumatch <{MAIL_SENDER_EMAIL}>',
            to_emails=to_email,
            subject=subject,
            plain_text_content=plain_content,
            html_content=html_content
        )
        sg.send(message)
    except Exception as e:
        app.logger.error(f'SendGrid mail failed: {e}')

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

@app.context_processor
def inject_notifications():
    if current_user.is_authenticated and current_user.role == 'applicant':
        notifs = (Notification.query
                  .filter_by(user_id=current_user.id)
                  .order_by(Notification.created_at.desc())
                  .limit(15).all())
        unread = sum(1 for n in notifs if not n.is_read)
        return {'notifs': notifs, 'notif_unread': unread}
    return {'notifs': [], 'notif_unread': 0}

with app.app_context():
    db.create_all()
    from sqlalchemy import text, inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    dialect = db.engine.dialect.name  # 'sqlite' or 'postgresql'
    bool_true = 'TRUE' if dialect == 'postgresql' else '1'

    if inspector.has_table('applications'):
        existing_ap = [c['name'] for c in inspector.get_columns('applications')]
        with db.engine.connect() as conn:
            if 'semantic_score' not in existing_ap:
                conn.execute(text('ALTER TABLE applications ADD COLUMN semantic_score FLOAT DEFAULT 0.0'))
            conn.commit()

    if inspector.has_table('users'):
        existing = [c['name'] for c in inspector.get_columns('users')]
        new_cols = {
            'phone':                 'VARCHAR(20)',
            'address1':              'VARCHAR(200)',
            'address2':              'VARCHAR(200)',
            'postcode':              'VARCHAR(10)',
            'state':                 'VARCHAR(50)',
            'verify_code':           'VARCHAR(6)',
            'is_verified':           f'BOOLEAN DEFAULT {bool_true}',
            'saved_resume_path':     'VARCHAR(255)',
            'saved_resume_filename': 'VARCHAR(255)',
            'saved_resume_text':     'TEXT',
        }
        with db.engine.connect() as conn:
            for col, col_type in new_cols.items():
                if col not in existing:
                    conn.execute(text(f'ALTER TABLE users ADD COLUMN {col} {col_type}'))
            conn.commit()

    if inspector.has_table('job_postings'):
        existing_jp = [c['name'] for c in inspector.get_columns('job_postings')]
        with db.engine.connect() as conn:
            for col in ('salary_min', 'salary_max'):
                if col not in existing_jp:
                    conn.execute(text(f'ALTER TABLE job_postings ADD COLUMN {col} INTEGER'))
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
            html = f"""
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
            plain = f'Hi {first_name}, your SmartResume verification code is: {code}'
            threading.Thread(target=send_email_async, args=(email, 'Your SmartResume Verification Code', html, plain), daemon=True).start()
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
    html = f"""
    <div style="font-family:sans-serif;max-width:420px;margin:0 auto;">
        <h2 style="color:#1e293b;">New Verification Code</h2>
        <p>Your new SmartResume verification code is:</p>
        <div style="background:#fff7ed;border-radius:12px;padding:24px;text-align:center;margin:20px 0;">
            <span style="font-size:42px;font-weight:900;letter-spacing:10px;color:#ea580c;">{code}</span>
        </div>
        <p style="color:#64748b;font-size:12px;">SmartResume &mdash; UOW Malaysia KDU</p>
    </div>"""
    plain = f'Your new SmartResume verification code is: {code}'
    threading.Thread(target=send_email_async, args=(user.email, 'Your SmartResume Verification Code', html, plain), daemon=True).start()
    flash('A new code has been sent to your email.')
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
    pending_apps = Application.query.join(JobPosting).filter(JobPosting.created_by == current_user.id, Application.status == 'submitted').order_by(Application.applied_at.asc()).all()
    return render_template('hr/dashboard.html', jobs=jobs, total_apps=total_apps, shortlisted=shortlisted, pending=pending, top_apps=top_apps, pending_apps=pending_apps)

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
        salary_min  = request.form.get('salary_min', '').strip() or None
        salary_max  = request.form.get('salary_max', '').strip() or None
        if salary_min: salary_min = int(salary_min)
        if salary_max: salary_max = int(salary_max)
        if not title or not description:
            flash('Job title and description are required.')
        else:
            job = JobPosting(title=title, company=company, location=location,
                             description=description, keywords=keywords,
                             salary_min=salary_min, salary_max=salary_max,
                             created_by=current_user.id)
            db.session.add(job)
            db.session.flush()
            applicants = User.query.filter_by(role='applicant', is_verified=True).all()
            for a in applicants:
                db.session.add(Notification(
                    user_id=a.id,
                    message=f'🔔 New job posted: {title} at {company} ({location})',
                    link=url_for('applicant_apply', job_id=job.id)
                ))
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
        salary_min  = request.form.get('salary_min', '').strip() or None
        salary_max  = request.form.get('salary_max', '').strip() or None
        if salary_min: salary_min = int(salary_min)
        if salary_max: salary_max = int(salary_max)
        if not title or not description:
            flash('Job title and description are required.')
        else:
            job.title       = title
            job.company     = company
            job.location    = location
            job.description = description
            job.salary_min  = salary_min
            job.salary_max  = salary_max
            job.keywords    = keywords
            db.session.commit()
            app_count = Application.query.filter_by(job_id=job_id).count()
            if app_count > 0:
                return redirect(url_for('hr_screening_results', job_id=job_id, confirm_rescore=1))
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

@app.route('/hr/jobs/<int:job_id>/rescore', methods=['POST'])
@login_required
@hr_required
def hr_rescore_job(job_id):
    job = JobPosting.query.get_or_404(job_id)
    applications = Application.query.filter_by(job_id=job_id).all()
    rescored = 0
    for appl in applications:
        if not appl.resume_text:
            continue
        old_score = appl.composite_score
        scores = compute_scores(appl.resume_text, job.description, job.keywords or '')
        appl.keyword_score    = scores['keyword_score']
        appl.fuzzy_score      = scores['fuzzy_score']
        appl.similarity_score = scores['similarity_score']
        appl.semantic_score   = scores['semantic_score']
        appl.composite_score  = scores['composite_score']
        appl.status           = 'submitted'
        db.session.add(Notification(
            user_id=appl.user_id,
            message=(f'📊 Your application for {job.title} has been re-scored '
                     f'({old_score}% → {scores["composite_score"]}%). '
                     f'Your application is now pending HR review again.'),
            link=url_for('applicant_application_detail', app_id=appl.id)
        ))
        rescored += 1
    db.session.commit()
    flash(f'Re-scored {rescored} application{"s" if rescored != 1 else ""}. All statuses reset to Pending Review.')
    return redirect(url_for('hr_screening_results', job_id=job_id))

@app.route('/hr/jobs/<int:job_id>/export-csv')
@login_required
@hr_required
def hr_export_csv(job_id):
    job = JobPosting.query.get_or_404(job_id)
    applications = Application.query.filter_by(job_id=job_id).order_by(Application.composite_score.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Rank', 'Name', 'Email', 'Composite Score (%)', 'Keyword Score (%)',
                     'Fuzzy Score (%)', 'Text Similarity (%)', 'Status', 'Applied Date'])
    for i, appl in enumerate(applications, 1):
        writer.writerow([i, appl.applicant.name, appl.applicant.email,
                         appl.composite_score, appl.keyword_score, appl.fuzzy_score,
                         appl.similarity_score, appl.status,
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
    db.session.add(Notification(
        user_id=appl.user_id,
        message=f'🎉 Your application for {appl.job.title} at {appl.job.company} has been shortlisted!',
        link=url_for('applicant_application_detail', app_id=appl.id)
    ))
    db.session.commit()
    name     = appl.applicant.name
    email    = appl.applicant.email
    job      = appl.job.title
    company  = appl.job.company
    score    = appl.composite_score
    html = f"""
<div style="font-family:Calibri,Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
  <div style="background:#1e293b;padding:24px 28px;">
    <div style="font-size:20px;font-weight:800;color:#ffffff;">Resumatch</div>
    <div style="font-size:12px;color:#94a3b8;margin-top:2px;">Smart Resume Screening</div>
  </div>
  <div style="padding:28px;">
    <div style="font-size:22px;font-weight:700;color:#16a34a;margin-bottom:6px;">&#x2705; Congratulations, {name}!</div>
    <p style="font-size:14px;color:#334155;line-height:1.7;margin-bottom:16px;">
      We are pleased to inform you that your application for <strong>{job}</strong> at <strong>{company}</strong>
      has been <strong style="color:#16a34a;">shortlisted</strong>.
    </p>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:14px 18px;margin-bottom:20px;">
      <div style="font-size:12px;color:#15803d;font-weight:700;margin-bottom:4px;">YOUR MATCH SCORE</div>
      <div style="font-size:28px;font-weight:900;color:#16a34a;">{score}%</div>
    </div>
    <p style="font-size:13px;color:#64748b;line-height:1.7;">
      The hiring team was impressed with your profile. They will be in touch with you shortly regarding the next steps.
      Please ensure your contact details are up to date.
    </p>
    <p style="font-size:13px;color:#64748b;margin-top:16px;">Best regards,<br><strong style="color:#1e293b;">The Resumatch Team</strong></p>
  </div>
  <div style="background:#f8fafc;padding:14px 28px;text-align:center;">
    <p style="font-size:11px;color:#94a3b8;margin:0;">This is an automated notification from the Smart Resume Screening System.</p>
  </div>
</div>"""
    plain = (f"Congratulations {name}! Your application for {job} at {company} has been shortlisted. "
             f"Your match score: {score}%. The hiring team will contact you soon.")
    threading.Thread(target=send_email_async, args=(email, f'Application Update — You have been shortlisted for {job}', html, plain), daemon=True).start()
    return redirect(request.referrer or url_for('hr_candidates'))

@app.route('/hr/applications/<int:app_id>/reject', methods=['POST'])
@login_required
@hr_required
def hr_reject_application(app_id):
    appl = Application.query.get_or_404(app_id)
    appl.status = 'rejected'
    db.session.add(Notification(
        user_id=appl.user_id,
        message=f'📋 A decision has been made on your {appl.job.title} application at {appl.job.company}.',
        link=url_for('applicant_application_detail', app_id=appl.id)
    ))
    db.session.commit()
    name     = appl.applicant.name
    email    = appl.applicant.email
    job      = appl.job.title
    company  = appl.job.company
    html = f"""
<div style="font-family:Calibri,Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
  <div style="background:#1e293b;padding:24px 28px;">
    <div style="font-size:20px;font-weight:800;color:#ffffff;">Resumatch</div>
    <div style="font-size:12px;color:#94a3b8;margin-top:2px;">Smart Resume Screening</div>
  </div>
  <div style="padding:28px;">
    <div style="font-size:20px;font-weight:700;color:#334155;margin-bottom:6px;">Application Status Update</div>
    <p style="font-size:14px;color:#334155;line-height:1.7;margin-bottom:16px;">
      Dear <strong>{name}</strong>,<br><br>
      Thank you for applying for <strong>{job}</strong> at <strong>{company}</strong>.
      After careful review, we regret to inform you that your application has <strong style="color:#dc2626;">not been selected</strong>
      to proceed to the next stage.
    </p>
    <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:14px 18px;margin-bottom:20px;">
      <p style="font-size:13px;color:#991b1b;margin:0;line-height:1.6;">
        We encourage you to continue improving your resume and applying for future openings that match your skill set.
        You can download our <strong>resume template</strong> from the applicant portal to maximise your match score.
      </p>
    </div>
    <p style="font-size:13px;color:#64748b;line-height:1.7;">
      We appreciate the time you invested in your application and wish you the best in your job search.
    </p>
    <p style="font-size:13px;color:#64748b;margin-top:16px;">Best regards,<br><strong style="color:#1e293b;">The Resumatch Team</strong></p>
  </div>
  <div style="background:#f8fafc;padding:14px 28px;text-align:center;">
    <p style="font-size:11px;color:#94a3b8;margin:0;">This is an automated notification from the Smart Resume Screening System.</p>
  </div>
</div>"""
    plain = (f"Dear {name}, thank you for applying for {job} at {company}. "
             f"After careful review, your application has not been selected to proceed. "
             f"We wish you the best in your job search.")
    threading.Thread(target=send_email_async, args=(email, f'Application Update — {job} at {company}', html, plain), daemon=True).start()
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
    q     = request.args.get('q', '').strip()
    loc_q = request.args.get('location', '').strip()

    jobs = JobPosting.query.filter_by(is_active=True).all()
    search_scores = {}

    if q:
        scored = []
        for job in jobs:
            job_text = f"{job.title} {job.keywords or ''} {job.description[:600]}"
            score = semantic_score(q, job_text)
            scored.append((job, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        scored = [(job, s) for job, s in scored if s >= 20.0]
        jobs = [job for job, s in scored]
        search_scores = {job.id: round(s) for job, s in scored}
    else:
        jobs = sorted(jobs, key=lambda j: j.created_at, reverse=True)

    if loc_q:
        loc_lower = loc_q.lower()
        jobs = [j for j in jobs if j.location and loc_lower in j.location.lower()]

    return render_template('applicant/browse_jobs.html', jobs=jobs,
                           search_scores=search_scores, search_q=q, search_loc=loc_q)

@app.route('/applicant/jobs/<int:job_id>/apply', methods=['GET', 'POST'])
@login_required
@applicant_required
def applicant_apply(job_id):
    job = JobPosting.query.get_or_404(job_id)
    if request.method == 'POST':
        if Application.query.filter_by(user_id=current_user.id, job_id=job_id).first():
            flash('You have already applied for this job.')
            return render_template('applicant/apply_job.html', job=job)

        resume_choice = request.form.get('resume_choice', 'upload')

        if resume_choice == 'saved':
            if not current_user.saved_resume_text:
                flash('No saved resume found in your profile. Please upload a resume.')
                return render_template('applicant/apply_job.html', job=job)
            filename    = current_user.saved_resume_path
            resume_text = current_user.saved_resume_text
        else:
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

@app.route('/applicant/notifications/<int:notif_id>/read')
@login_required
@applicant_required
def applicant_read_notification(notif_id):
    notif = Notification.query.get_or_404(notif_id)
    if notif.user_id == current_user.id:
        notif.is_read = True
        db.session.commit()
    return redirect(notif.link or url_for('applicant_dashboard'))

@app.route('/applicant/notifications/read-all', methods=['POST'])
@login_required
@applicant_required
def applicant_read_all_notifications():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return redirect(request.referrer or url_for('applicant_dashboard'))

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

@app.route('/applicant/settings/resume', methods=['POST'])
@login_required
@applicant_required
def applicant_save_resume():
    file = request.files.get('resume')
    if not file or not file.filename or not allowed_file(file.filename):
        flash('Please upload a valid PDF or DOCX file.')
        return redirect(url_for('applicant_settings'))
    filename = secure_filename(f'profile_{current_user.id}_{file.filename}')
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    resume_text = extract_text(filepath)
    if not resume_text:
        flash('Could not extract text from that file. Try a different PDF or DOCX.')
        return redirect(url_for('applicant_settings'))
    current_user.saved_resume_path     = filename
    current_user.saved_resume_filename = file.filename
    current_user.saved_resume_text     = resume_text
    db.session.commit()
    flash('Resume saved to your profile successfully.')
    return redirect(url_for('applicant_settings'))

@app.route('/applicant/settings/resume/delete', methods=['POST'])
@login_required
@applicant_required
def applicant_delete_resume():
    current_user.saved_resume_path     = None
    current_user.saved_resume_filename = None
    current_user.saved_resume_text     = None
    db.session.commit()
    flash('Saved resume removed from your profile.')
    return redirect(url_for('applicant_settings'))

@app.route('/applicant/upload')
@login_required
@applicant_required
def applicant_upload():
    return render_template('applicant/upload_resume.html')

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
