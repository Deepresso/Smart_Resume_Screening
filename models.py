from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    email       = db.Column(db.String(150), unique=True, nullable=False)
    password    = db.Column(db.String(255), nullable=False)
    role        = db.Column(db.String(20), nullable=False)  # 'hr' or 'applicant'
    phone       = db.Column(db.String(20))
    address1    = db.Column(db.String(200))
    address2    = db.Column(db.String(200))
    postcode    = db.Column(db.String(10))
    state       = db.Column(db.String(50))
    is_verified = db.Column(db.Boolean, default=False)
    verify_code = db.Column(db.String(6))

    job_postings = db.relationship('JobPosting', backref='creator', lazy=True)
    applications = db.relationship('Application', backref='applicant', lazy=True)


class JobPosting(db.Model):
    __tablename__ = 'job_postings'

    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(150), nullable=False)
    company     = db.Column(db.String(150), nullable=False)
    location    = db.Column(db.String(100))
    description = db.Column(db.Text, nullable=False)
    keywords    = db.Column(db.Text)           # comma-separated keywords
    created_by  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    is_active   = db.Column(db.Boolean, default=True)

    applications = db.relationship('Application', backref='job', lazy=True)


class Application(db.Model):
    __tablename__ = 'applications'

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    job_id          = db.Column(db.Integer, db.ForeignKey('job_postings.id'), nullable=False)
    resume_path     = db.Column(db.String(255))        # path to uploaded resume file
    resume_text     = db.Column(db.Text)               # extracted text from resume
    cover_letter    = db.Column(db.Text)
    keyword_score   = db.Column(db.Float, default=0.0)
    fuzzy_score     = db.Column(db.Float, default=0.0)
    similarity_score= db.Column(db.Float, default=0.0)
    composite_score = db.Column(db.Float, default=0.0)
    status          = db.Column(db.String(30), default='submitted')  # submitted, review, shortlisted, rejected
    applied_at      = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    __tablename__ = 'notifications'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message    = db.Column(db.String(300), nullable=False)
    link       = db.Column(db.String(200))
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='notifications')
