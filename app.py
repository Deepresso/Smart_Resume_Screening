import os
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from models import db, User, JobPosting, Application
from screening import extract_text, compute_scores

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///smart_resume.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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
    # Seed default HR account if none exists
    if not User.query.filter_by(role='hr').first():
        hashed = bcrypt.generate_password_hash('admin123').decode('utf-8')
        hr = User(name='HR Admin', email='admin@smartresume.com', password=hashed, role='hr')
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
        password   = request.form.get('password', '')
        confirm    = request.form.get('confirm_password', '')
        if not all([first_name, last_name, email, password]):
            flash('Please fill in all required fields.')
        elif password != confirm:
            flash('Passwords do not match.')
        elif User.query.filter_by(email=email).first():
            flash('An account with this email already exists.')
        else:
            hashed = bcrypt.generate_password_hash(password).decode('utf-8')
            user = User(name=f'{first_name} {last_name}', email=email, password=hashed, role='applicant')
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for('applicant_dashboard'))
    return render_template('auth/register.html')

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

@app.route('/hr/jobs/<int:job_id>/results')
@login_required
@hr_required
def hr_screening_results(job_id):
    job = JobPosting.query.get_or_404(job_id)
    applications = Application.query.filter_by(job_id=job_id).order_by(Application.composite_score.desc()).all()
    return render_template('hr/screening_results.html', job=job, applications=applications)

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
    return render_template('hr/candidate_detail.html', appl=appl, rank=rank, total=total)

@app.route('/hr/candidates')
@login_required
@hr_required
def hr_candidates():
    applications = Application.query.join(JobPosting).filter(JobPosting.created_by == current_user.id).order_by(Application.composite_score.desc()).all()
    return render_template('hr/candidates.html', applications=applications)

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
