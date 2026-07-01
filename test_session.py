"""
test_session.py — End-to-end test data seeder for Smart Resume Screening System.

Runs DIRECTLY in Flask app context — no HTTP server needed, no email verification.
Creates 10 job postings + 20 pre-verified applicant accounts, then submits all
20 sample resumes to their matching jobs with full NLP scoring.

Usage:
    py test_session.py              # seed test data (skips existing accounts)
    py test_session.py --clean      # wipe all previous test data, then reseed
    py test_session.py --report     # print scores of existing test applications
"""

import os, sys, shutil
sys.path.insert(0, os.path.dirname(__file__))

from werkzeug.security import generate_password_hash
from app import app, db
from models import User, JobPosting, Application
from screening import extract_text, compute_scores

# ── Constants ─────────────────────────────────────────────────────────────────

RESUME_DIR   = os.path.join(os.path.dirname(__file__), 'sample_resumes')
UPLOAD_DIR   = os.path.join(os.path.dirname(__file__), 'uploads')
TEST_MARKER  = '[TEST]'
TEST_DOMAIN  = '@testresume.local'
HR_EMAIL     = 'admin@smartresume.com'


# ── 10 Job Postings ───────────────────────────────────────────────────────────

JOBS = [
    {
        'title':    f'Software Engineer {TEST_MARKER}',
        'company':  'TechCorp Malaysia',
        'location': 'Penang',
        'keywords': 'Python,Flask,REST API,PostgreSQL,SQL,OOP,Git,Docker,JavaScript,Unit Testing',
        'description': """\
We are looking for a Software Engineer to join our backend team building scalable REST APIs
and web applications using Python and modern frameworks.

Responsibilities:
- Design and implement Python-based REST API endpoints using Flask or FastAPI
- Write and optimize SQL queries for PostgreSQL and MySQL databases
- Apply OOP design patterns and write clean, maintainable, well-tested code
- Collaborate through Git version control with branching and code review workflows
- Write unit tests and integration tests using pytest to ensure code correctness
- Containerise services using Docker for consistent deployment across environments

Requirements:
- Proficiency in Python and JavaScript programming languages
- Experience with Flask, FastAPI, or similar Python web frameworks
- Strong knowledge of PostgreSQL or MySQL relational databases and SQL
- Solid understanding of OOP principles, REST API design, and MVC architecture
- Experience with Git, GitHub, and Agile/Scrum software development methodology
- Knowledge of Docker containerisation and basic CI/CD pipelines
""",
    },
    {
        'title':    f'Data Analyst {TEST_MARKER}',
        'company':  'DataVision Sdn Bhd',
        'location': 'Kuala Lumpur',
        'keywords': 'SQL,Python,pandas,Power BI,Tableau,ETL,data cleaning,PostgreSQL,Excel,statistics,NumPy',
        'description': """\
We are hiring a Data Analyst to transform raw data into clear, actionable business insights
using SQL, Python, and leading visualisation tools.

Responsibilities:
- Write complex SQL queries to extract, clean, and transform data from PostgreSQL databases
- Build interactive dashboards using Power BI and Tableau for business stakeholders
- Perform exploratory data analysis (EDA) using Python with pandas, NumPy, matplotlib, seaborn
- Design and maintain ETL pipelines to move data between source systems and data warehouse
- Conduct A/B testing, cohort analysis, and statistical analysis to support decisions
- Automate repetitive Excel-based reporting with Python scripts

Requirements:
- Strong SQL skills including window functions, CTEs, and joins (PostgreSQL, MySQL)
- Proficiency in Python data libraries: pandas, NumPy, matplotlib, seaborn, Jupyter Notebook
- Experience building dashboards and reports in Power BI or Tableau
- Knowledge of ETL processes, data warehousing, and data cleaning best practices
- Understanding of statistics: regression analysis, A/B testing, forecasting
- Excel proficiency: pivot tables, VLOOKUP, charting
""",
    },
    {
        'title':    f'UI/UX Designer {TEST_MARKER}',
        'company':  'Creative Digital Agency',
        'location': 'Penang',
        'keywords': 'Figma,Adobe XD,wireframing,prototyping,user research,usability testing,design system,Adobe Illustrator,UX',
        'description': """\
We are seeking a talented UI/UX Designer to craft intuitive, visually compelling digital
experiences for web and mobile platforms.

Responsibilities:
- Create wireframes, user flows, and high-fidelity prototypes using Figma and Adobe XD
- Conduct user research including stakeholder interviews, usability testing, and journey mapping
- Design and maintain a component-level design system in Figma for consistent UI
- Collaborate with frontend developers to ensure pixel-perfect implementation
- Apply accessibility standards (WCAG) and inclusive design principles
- Iterate on designs continuously based on usability testing feedback

Requirements:
- Expert proficiency in Figma, Adobe XD, Adobe Illustrator, and Adobe Photoshop
- Strong skills in wireframing, interactive prototyping, and high-fidelity UI design
- Experience conducting user research, usability testing, and A/B testing sessions
- Ability to build and maintain design systems and reusable component libraries
- Basic understanding of HTML and CSS for effective developer collaboration and handoff
- Portfolio demonstrating strong mobile and web interface design work
""",
    },
    {
        'title':    f'Network Engineer {TEST_MARKER}',
        'company':  'ConnectNet Solutions',
        'location': 'Cyberjaya',
        'keywords': 'TCP/IP,Cisco,CCNA,VLAN,BGP,OSPF,DNS,DHCP,VPN,firewall,routing,SolarWinds,Wireshark,Linux',
        'description': """\
We are looking for a Network Engineer to design, deploy, and manage enterprise LAN, WAN,
and data centre network infrastructure using Cisco technologies.

Responsibilities:
- Configure and maintain Cisco routers, Cisco switches, and Cisco ASA firewalls
- Design and implement VLAN segmentation and inter-VLAN routing for enterprise networks
- Manage BGP and OSPF routing protocols for WAN and data centre connectivity
- Configure and troubleshoot VPN tunnels (IPSec, SSL) for secure remote access
- Monitor network performance and anomalies using SolarWinds and Wireshark
- Maintain DNS, DHCP, NAT, and firewall ACL configurations

Requirements:
- Strong knowledge of TCP/IP networking protocols and network architecture
- Hands-on experience configuring Cisco IOS on Cisco routers and switches
- Proficiency with VLAN, BGP, OSPF, firewall configuration, and network access control
- Experience with DNS, DHCP, VPN, and NAT in enterprise environments
- Familiarity with network monitoring tools such as SolarWinds or PRTG
- Cisco CCNA or CCNP certification is required; Linux skills are an advantage
""",
    },
    {
        'title':    f'Cybersecurity Analyst {TEST_MARKER}',
        'company':  'SecureShield Malaysia',
        'location': 'Kuala Lumpur',
        'keywords': 'SIEM,Splunk,penetration testing,OWASP,vulnerability assessment,incident response,Nmap,Nessus,Burp Suite,Metasploit,cybersecurity',
        'description': """\
We are hiring a Cybersecurity Analyst to protect our digital infrastructure through proactive
threat detection, vulnerability assessment, and rapid incident response.

Responsibilities:
- Monitor SIEM dashboards (Splunk, IBM QRadar) daily for security threats and anomalies
- Conduct vulnerability assessments using Nmap, Nessus, and OWASP ZAP on internal and external assets
- Perform penetration testing on web applications following OWASP Top 10 methodology
- Manage security incidents end-to-end: containment, forensic analysis, and remediation
- Build and tune Splunk correlation rules and alerts to reduce false positives
- Develop and deliver cybersecurity awareness training programmes for staff

Requirements:
- Hands-on experience with SIEM platforms: Splunk, IBM QRadar, or Microsoft Sentinel
- Proficiency with penetration testing tools: Metasploit, Burp Suite, Nmap, Nessus, OWASP ZAP
- In-depth knowledge of OWASP Top 10 vulnerabilities and mitigation strategies
- Experience in incident response procedures, digital forensics, and malware triage
- Familiarity with security frameworks: NIST CSF, MITRE ATT&CK, ISO 27001
- CompTIA Security+, CEH, or CISSP certification preferred
""",
    },
    {
        'title':    f'Machine Learning Engineer {TEST_MARKER}',
        'company':  'AI Innovations Sdn Bhd',
        'location': 'Penang',
        'keywords': 'Python,PyTorch,TensorFlow,scikit-learn,NLP,machine learning,deep learning,BERT,HuggingFace,Docker,AWS,MLflow,pandas',
        'description': """\
We are looking for a Machine Learning Engineer to build, train, and deploy production-grade
ML models that serve millions of predictions daily.

Responsibilities:
- Design and train machine learning models using PyTorch, TensorFlow, and scikit-learn
- Build NLP pipelines: text classification, named entity recognition, and sentiment analysis
- Fine-tune transformer models (BERT, GPT) using the HuggingFace Transformers library
- Build ML pipelines and track experiments using MLflow and Apache Airflow
- Deploy models as REST APIs using FastAPI and Docker on AWS SageMaker or GCP Vertex AI
- Perform feature engineering and data preprocessing using pandas and NumPy

Requirements:
- Strong proficiency in Python and deep learning frameworks: PyTorch and TensorFlow
- Experience with scikit-learn, XGBoost, or LightGBM for classical machine learning
- Knowledge of NLP techniques: word embeddings, transformers, BERT, tokenisation
- Hands-on model deployment experience using Docker and AWS or GCP cloud platforms
- Familiarity with MLflow for experiment tracking and model versioning and management
- Strong data manipulation skills with pandas and NumPy in Jupyter Notebook environments
""",
    },
    {
        'title':    f'Full Stack Web Developer {TEST_MARKER}',
        'company':  'WebForge Digital',
        'location': 'Johor Bahru',
        'keywords': 'React,Node.js,JavaScript,TypeScript,PostgreSQL,MongoDB,REST API,Docker,GitHub Actions,Tailwind CSS,Express,Jest',
        'description': """\
We are seeking a Full Stack Web Developer with strong React and Node.js expertise to design
and build modern, scalable web applications end to end.

Responsibilities:
- Build responsive and accessible frontend interfaces using React, TypeScript, and Tailwind CSS
- Develop backend REST APIs using Node.js and Express with proper validation and error handling
- Design and manage PostgreSQL and MongoDB database schemas for production workloads
- Implement JWT-based authentication and role-based access control across the application
- Containerise applications with Docker and set up CI/CD pipelines using GitHub Actions
- Write unit tests with Jest and integration tests to maintain code quality and coverage

Requirements:
- Strong proficiency in React, JavaScript (ES6+), and TypeScript for frontend development
- Backend development experience with Node.js, Express, and RESTful API design
- Solid database skills across PostgreSQL, MongoDB, and Redis
- Comfortable with Docker containerisation and GitHub Actions CI/CD workflows
- Experience with Tailwind CSS and building fully responsive web interfaces
- Understanding of JWT authentication, web security best practices, and OWASP principles
""",
    },
    {
        'title':    f'Cloud Infrastructure Engineer {TEST_MARKER}',
        'company':  'CloudBase Solutions',
        'location': 'Kuala Lumpur',
        'keywords': 'AWS,Terraform,Docker,Kubernetes,CI/CD,Linux,IAM,EC2,S3,RDS,GitHub Actions,EKS,infrastructure',
        'description': """\
We are looking for a Cloud Infrastructure Engineer to design, automate, and manage highly
available AWS cloud environments using infrastructure-as-code and container orchestration.

Responsibilities:
- Design and provision AWS infrastructure (EC2, S3, RDS, VPC, Lambda, EKS, IAM) using Terraform
- Build and maintain Kubernetes clusters (EKS) for containerised microservice workloads
- Implement CI/CD pipelines using GitHub Actions, Jenkins, or ArgoCD for 0-downtime releases
- Enforce IAM least-privilege policies and AWS security best practices across all environments
- Monitor infrastructure using CloudWatch, Grafana, and PRTG dashboards
- Write reusable Terraform modules and Ansible playbooks for infrastructure automation

Requirements:
- Hands-on AWS experience: EC2, S3, RDS, IAM, VPC, Lambda, EKS, CloudFormation, CloudWatch
- Proficiency in Terraform for infrastructure-as-code provisioning and management
- Experience with Docker containerisation and Kubernetes orchestration (EKS, kubectl, helm)
- Knowledge of CI/CD toolchain: GitHub Actions, Jenkins, GitLab CI, or ArgoCD
- Strong Linux administration and bash scripting skills
- AWS Solutions Architect or DevOps Engineer Professional certification preferred
""",
    },
    {
        'title':    f'Android Mobile Developer {TEST_MARKER}',
        'company':  'MobileFirst Studio',
        'location': 'Shah Alam',
        'keywords': 'Kotlin,Android,Jetpack Compose,Firebase,MVVM,REST API,Room,Google Play,Retrofit,ViewModel,LiveData',
        'description': """\
We are hiring an Android Mobile Developer to build polished, high-performance Android
applications using modern Kotlin and Jetpack component libraries.

Responsibilities:
- Develop Android applications using Kotlin and Jetpack Compose for declarative UI
- Implement MVVM architecture with ViewModel, LiveData, and Repository pattern
- Integrate REST APIs using Retrofit and OkHttp for backend communication
- Manage local data persistence using the Room database library
- Integrate Firebase services: Authentication, Firestore, and Cloud Messaging for push notifications
- Publish, update, and monitor Android apps on the Google Play Console

Requirements:
- Strong Kotlin proficiency and deep Android development experience
- Expertise in Jetpack libraries: Compose, ViewModel, LiveData, Room, Navigation Component
- Thorough understanding of MVVM and Repository design patterns for Android
- Experience consuming REST APIs using Retrofit and managing background tasks
- Hands-on Firebase experience: Authentication, Firestore, Cloud Messaging, Analytics
- Track record of publishing production Android apps on Google Play
- Familiarity with JUnit testing and Espresso UI testing frameworks
""",
    },
    {
        'title':    f'IT Support Specialist {TEST_MARKER}',
        'company':  'TechSupport Pro',
        'location': 'Penang',
        'keywords': 'Windows,Active Directory,Microsoft 365,TCP/IP,DNS,DHCP,helpdesk,troubleshooting,ITIL,ServiceNow,Group Policy',
        'description': """\
We are looking for an IT Support Specialist to provide reliable technical support and
maintain IT infrastructure for our 800-person organisation.

Responsibilities:
- Provide Level 1 to Level 3 helpdesk support for hardware, software, and networking issues
- Administer Active Directory: create and manage user accounts, OUs, and Group Policy Objects (GPO)
- Manage Microsoft 365 services: Exchange Online, SharePoint, Teams, OneDrive, and Intune MDM
- Troubleshoot TCP/IP, DNS, DHCP, VPN, and Wi-Fi connectivity issues across the network
- Set up and configure Windows 10/11 workstations and Windows Server environments
- Log, prioritise, and resolve support tickets using ServiceNow or Jira Service Management

Requirements:
- Proficiency with Windows 10, Windows 11, and Windows Server administration
- Strong Active Directory management including Group Policy (GPO) creation and troubleshooting
- Hands-on experience administering Microsoft 365 (Exchange, Teams, SharePoint, Intune)
- Solid understanding of TCP/IP, DNS, DHCP, VPN, and wireless networking concepts
- Familiarity with ITSM ticketing tools: ServiceNow or Jira Service Management
- CompTIA A+, Network+, Microsoft 365 Certified, or ITIL Foundation certification preferred
""",
    },
]


# ── 20 Test Applicants (email, display name, resume file, job index 0-9) ──────

APPLICANTS = [
    ('test_se_junior@testresume.local',  'Ahmad Faris bin Rosli',       '01_software_engineer_junior_AhmadFaris.docx',     0),
    ('test_se_senior@testresume.local',  'Lim Wei Jian',                '02_software_engineer_senior_LimWeiJian.docx',     0),
    ('test_da_junior@testresume.local',  'Nurul Ain binti Azman',       '03_data_analyst_junior_NurulAin.docx',            1),
    ('test_da_senior@testresume.local',  'Rajesh Kumar Nair',           '04_data_analyst_senior_RajeshNair.docx',          1),
    ('test_ux_junior@testresume.local',  'Siti Nabilah binti Zulkifli', '05_ux_designer_junior_SitiNabilah.docx',          2),
    ('test_ux_senior@testresume.local',  'Kevin Tan Chee Seng',         '06_ux_designer_senior_KevinTan.docx',             2),
    ('test_ne_junior@testresume.local',  'Muhammad Haziq bin Hamdan',   '07_network_engineer_junior_MuhammadHaziq.docx',   3),
    ('test_ne_senior@testresume.local',  'David Chong Wai Kit',         '08_network_engineer_senior_DavidChong.docx',      3),
    ('test_cs_junior@testresume.local',  'Amirul Hakim bin Saifuddin',  '09_cybersecurity_analyst_junior_AmirulHakim.docx',4),
    ('test_cs_senior@testresume.local',  'Priya Menon',                 '10_cybersecurity_analyst_senior_PriyaMenon.docx', 4),
    ('test_ml_junior@testresume.local',  'Tan Jia Hui',                 '11_ml_engineer_junior_TanJiaHui.docx',            5),
    ('test_ml_senior@testresume.local',  'Dr Chandra Sekaran',          '12_ml_engineer_senior_ChandraSekaran.docx',       5),
    ('test_fs_junior@testresume.local',  'Nur Izzati binti Hashim',     '13_fullstack_developer_junior_NurIzzati.docx',    6),
    ('test_fs_senior@testresume.local',  'Jason Lee Kok Weng',          '14_fullstack_developer_senior_JasonLee.docx',     6),
    ('test_ce_junior@testresume.local',  'Hafizuddin bin Mohd Noor',    '15_cloud_engineer_junior_Hafizuddin.docx',        7),
    ('test_ce_senior@testresume.local',  'Vincent Yap Boon Kiat',       '16_cloud_engineer_senior_VincentYap.docx',        7),
    ('test_and_junior@testresume.local', 'Luqmanul Hakim bin Roslan',   '17_android_developer_junior_LuqmanulHakim.docx',  8),
    ('test_and_senior@testresume.local', 'Grace Ong Mei Ling',          '18_android_developer_senior_GraceOng.docx',       8),
    ('test_it_junior@testresume.local',  'Mohamad Syafiq bin Ismail',   '19_it_support_junior_MohamadSyafiq.docx',         9),
    ('test_it_senior@testresume.local',  'Rathi Krishnaswamy',          '20_it_support_senior_RathiKrishnaswamy.docx',     9),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg):
    print(msg)


def clean():
    log('\n── Cleaning previous test data ──────────────────────────────')
    # Delete applications linked to test jobs or test users
    test_job_ids = [j.id for j in JobPosting.query.filter(JobPosting.title.contains(TEST_MARKER)).all()]
    test_user_ids = [u.id for u in User.query.filter(User.email.contains(TEST_DOMAIN)).all()]
    deleted_apps = Application.query.filter(
        (Application.job_id.in_(test_job_ids)) | (Application.user_id.in_(test_user_ids))
    ).delete(synchronize_session=False)
    log(f'  Deleted {deleted_apps} application(s)')

    deleted_jobs = JobPosting.query.filter(JobPosting.title.contains(TEST_MARKER)).delete(synchronize_session=False)
    log(f'  Deleted {deleted_jobs} job posting(s)')

    deleted_users = User.query.filter(User.email.contains(TEST_DOMAIN)).delete(synchronize_session=False)
    log(f'  Deleted {deleted_users} test user(s)')

    db.session.commit()
    log('  Done.\n')


def seed():
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Find HR user
    hr = User.query.filter_by(email=HR_EMAIL).first()
    if not hr:
        log(f'ERROR: HR account {HR_EMAIL} not found. Start the app once first to seed the admin account.')
        return

    # ── Create 10 job postings ────────────────────────────────────────────────
    log('── Creating job postings ─────────────────────────────────────')
    created_jobs = []
    for j in JOBS:
        job = JobPosting(
            title=j['title'],
            company=j['company'],
            location=j['location'],
            description=j['description'].strip(),
            keywords=j['keywords'],
            created_by=hr.id,
            is_active=True,
        )
        db.session.add(job)
        db.session.flush()  # get job.id before commit
        created_jobs.append(job)
        log(f'  [{job.id:>3}] {job.title}')
    db.session.commit()

    # ── Create applicants and submit resumes ──────────────────────────────────
    log('\n── Submitting resumes ────────────────────────────────────────')
    results = []
    for email, name, resume_file, job_idx in APPLICANTS:
        job = created_jobs[job_idx]

        # Find or create applicant account
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                name=name,
                email=email,
                password=generate_password_hash('Test1234!'),
                role='applicant',
                phone='0123456789',
                address1='123 Test Street',
                postcode='10050',
                state='Penang',
                is_verified=True,
            )
            db.session.add(user)
            db.session.flush()

        # Skip if already applied to this exact job
        existing = Application.query.filter_by(user_id=user.id, job_id=job.id).first()
        if existing:
            log(f'  [SKIP] {name} already applied to {job.title}')
            results.append((name, job.title, existing.composite_score,
                            existing.keyword_score, existing.fuzzy_score, existing.similarity_score))
            continue

        # Copy resume to uploads folder
        src = os.path.join(RESUME_DIR, resume_file)
        if not os.path.exists(src):
            log(f'  [WARN] Resume file not found: {src}')
            continue
        dest_filename = f'test_{user.id}_{job.id}_{resume_file}'
        dest_path = os.path.join(UPLOAD_DIR, dest_filename)
        shutil.copy2(src, dest_path)

        # Extract text and score
        resume_text = extract_text(dest_path)
        scores = compute_scores(resume_text, job.description, job.keywords or '')

        application = Application(
            user_id=user.id,
            job_id=job.id,
            resume_path=dest_filename,
            resume_text=resume_text,
            cover_letter='',
            keyword_score=scores['keyword_score'],
            fuzzy_score=scores['fuzzy_score'],
            similarity_score=scores['similarity_score'],
            semantic_score=scores['semantic_score'],
            composite_score=scores['composite_score'],
            status='submitted',
        )
        db.session.add(application)
        results.append((name, job.title, scores['composite_score'],
                        scores['keyword_score'], scores['fuzzy_score'], scores['similarity_score']))
        log(f'  {name:<35} → {job.title:<40} composite={scores["composite_score"]:5.1f}%')

    db.session.commit()
    return results


def report():
    log('\n── Test Session Score Report ─────────────────────────────────')
    log(f'{"#":<3} {"Applicant":<35} {"Job":<32} {"KW":>5} {"FZ":>5} {"SIM":>5} {"TOT":>5}')
    log('─' * 95)
    apps = (
        Application.query
        .join(User, Application.user_id == User.id)
        .join(JobPosting, Application.job_id == JobPosting.id)
        .filter(User.email.contains(TEST_DOMAIN))
        .order_by(JobPosting.title, Application.composite_score.desc())
        .all()
    )
    for i, a in enumerate(apps, 1):
        log(f'{i:<3} {a.applicant.name:<35} {a.job.title[:30]:<32} '
            f'{a.keyword_score:>5.1f} {a.fuzzy_score:>5.1f} '
            f'{a.similarity_score:>5.1f} {a.composite_score:>5.1f}')
    if not apps:
        log('  No test applications found. Run: py test_session.py')
    log('')


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = set(sys.argv[1:])
    with app.app_context():
        if '--report' in args:
            report()
        elif '--clean' in args:
            clean()
            seed()
            report()
        else:
            seed()
            report()
