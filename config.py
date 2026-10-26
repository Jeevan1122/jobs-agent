import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Gmail
GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Candidate profile
CANDIDATE = {
    "name": "Jeevan Sai Kodamati",
    "email": "kodamatijeevan@gmail.com",
    "phone": "+1 (405) 856-3338",
    "linkedin": "https://www.linkedin.com/in/kodamati-jeevan-sai-4b5390195/",
    "github": "https://github.com/Jeevan1122",
    "visa_status": "F-1 STEM OPT",
    "opt_expiry": "July 2028",
    "work_authorization": "W-2 only — cannot work 1099 or C2C on F-1 OPT",
}

# Job search config
JOB_SEARCH = {
    "search_terms": [
        "Data Engineer",
        "GCP Data Engineer",
        "AI Platform Engineer",
        "Full Stack Data Engineer",
        "Senior Data Engineer",
        "Cloud Data Engineer",
    ],
    "location": "United States",
    "remote": True,
    "results_per_term": 20,
    "min_ats_score": 65,
    "sites": ["linkedin", "indeed", "zip_recruiter"],
}

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESUME_DIR = os.path.join(BASE_DIR, "resume")
MASTER_RESUME_PATH = os.path.join(RESUME_DIR, "master_resume.tex")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DB_PATH = os.path.join(BASE_DIR, "db", "applications.db")

# Hard disqualifier phrases (case-insensitive)
VISA_HARD_DISQUALIFIERS = [
    "no sponsorship",
    "without sponsorship",
    "sponsorship not available",
    "sponsorship is not available",
    "cannot sponsor",
    "unable to sponsor",
    "does not sponsor",
    "not able to sponsor",
    "u.s. citizenship required",
    "us citizenship required",
    "must be a u.s. citizen",
    "must be a us citizen",
    "active security clearance",
    "secret clearance",
    "top secret",
    "itar",
    "1099 only",
    "c2c only",
    "corp to corp only",
    "f-1 not eligible",
    "opt not eligible",
    "2027 new grad",
    "class of 2027 only",
]

# Gray area phrases — flag for manual review, don't auto-reject
VISA_GRAY_AREA = [
    "may not be able to employ",
    "employment eligibility will be verified",
    "legally authorized",
    "work authorization required",
]

# Skills Jeevan actually has (for ATS scoring)
CANDIDATE_SKILLS = [
    "Python", "SQL", "Java", "Go",
    "GCP", "BigQuery", "Dataflow", "Pub/Sub", "Cloud Composer", "Dataproc",
    "Vertex AI", "Cloud Functions", "Cloud Storage", "GCP SCC",
    "AWS", "S3", "Lambda", "Redshift", "Glue", "EMR", "Kinesis", "Bedrock",
    "Inspector v2", "Cognito", "Secrets Manager", "API Gateway",
    "Azure", "ADF", "Synapse", "ADLS",
    "Apache Kafka", "Kafka", "Apache Spark", "PySpark", "Spark",
    "Hadoop", "HDFS", "Hive", "Databricks", "Delta Lake",
    "ETL", "ELT", "ETL/ELT", "data pipeline", "data warehouse",
    "data modeling", "data governance", "data quality",
    "Docker", "GitHub Actions", "CI/CD", "Terraform", "Git",
    "PostgreSQL", "SQLite", "REST API",
    "React", "Node.js", "Express",
    "Power BI", "Looker Studio", "Tableau",
    "JIRA", "Agile", "Scrum",
    "AWS Bedrock", "RAG", "GPT-4", "Gemini", "LLM", "MCP",
    "agentic", "AI", "machine learning",
    "Linux", "Airflow",
]
