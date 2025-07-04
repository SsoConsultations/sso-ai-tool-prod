import streamlit as st
import os
import io
import json
import re
import bcrypt
from datetime import datetime
import time
import base64

# --- Firebase Imports ---
import firebase_admin
from firebase_admin import credentials, auth, firestore
from firebase_admin import exceptions

# --- Pyrebase for email/password login ---
import pyrebase

# --- Google Drive Imports ---
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- AI & Document Processing Imports ---
from openai import OpenAI
import pandas as pd
from PyPDF2 import PdfReader
from docx import Document

# --- Streamlit Page Configuration ---
st.set_page_config(
    page_title="SSO Consultants AI Recruitment",
    page_icon="sso_logo.png",
    layout="wide"
)

# --- Pyrebase config ---
pyrebase_config = {
    "apiKey": "YOUR_API_KEY",
    "authDomain": "YOUR_PROJECT_ID.firebaseapp.com",
    "databaseURL": "",
    "projectId": "YOUR_PROJECT_ID",
    "storageBucket": "YOUR_PROJECT_ID.appspot.com",
    "messagingSenderId": "YOUR_MESSAGING_SENDER_ID",
    "appId": "YOUR_APP_ID"
}

firebase_pyrebase = pyrebase.initialize_app(pyrebase_config)
pyre_auth = firebase_pyrebase.auth()

# --- Custom CSS ---
st.markdown(
    """
    <style>
    body {background-color: #FFFFFF; color: #000000;}
    .stButton>button {background-color: #4CAF50; color: white;}
    </style>
    """,
    unsafe_allow_html=True
)

# --- Session State Initialization ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "user_email" not in st.session_state:
    st.session_state["user_email"] = None
if "user_uid" not in st.session_state:
    st.session_state["user_uid"] = None
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False
if "login_mode" not in st.session_state:
    st.session_state["login_mode"] = "choose_role"
if "is_admin_attempt" not in st.session_state:
    st.session_state["is_admin_attempt"] = False
if "username" not in st.session_state:
    st.session_state["username"] = None
if "has_set_username" not in st.session_state:
    st.session_state["has_set_username"] = False
if "needs_username_setup" not in st.session_state:
    st.session_state["needs_username_setup"] = False
if "login_success" not in st.session_state:
    st.session_state["login_success"] = False
if "current_admin_page" not in st.session_state:
    st.session_state["current_admin_page"] = "reports"

# --- Firebase Admin Initialization ---
if not firebase_admin._apps:
    try:
        firebase_service_account_json_str = st.secrets["FIREBASE_SERVICE_ACCOUNT_KEY"]
        firebase_service_account_info = json.loads(firebase_service_account_json_str)
        if "private_key" in firebase_service_account_info:
            firebase_service_account_info["private_key"] = firebase_service_account_info["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(firebase_service_account_info)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
    except Exception as e:
        st.error(f"Firebase initialization error: {e}")
        st.stop()
else:
    db = firestore.client()

# --- Google Drive Initialization ---
try:
    google_drive_key_json_str = st.secrets["GOOGLE_DRIVE_KEY"]
    google_drive_key_info = json.loads(google_drive_key_json_str)
    if "private_key" in google_drive_key_info:
        google_drive_key_info["private_key"] = google_drive_key_info["private_key"].replace("\\n", "\n")
    SCOPES = ["https://www.googleapis.com/auth/drive"]
    drive_credentials = service_account.Credentials.from_service_account_info(google_drive_key_info, scopes=SCOPES)
    drive_service = build("drive", "v3", credentials=drive_credentials)
except Exception as e:
    st.error(f"Google Drive initialization error: {e}")
    st.stop()

# --- OpenAI Initialization ---
try:
    openai_api_key = st.secrets["OPENAI_API_KEY"]
    openai_client = OpenAI(api_key=openai_api_key)
except Exception as e:
    st.error(f"OpenAI initialization error: {e}")
    st.stop()

GOOGLE_DRIVE_REPORTS_FOLDER_ID = st.secrets.get("GOOGLE_DRIVE_REPORTS_FOLDER_ID")
# --- Utility Functions ---
def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def check_password(password, hashed_password):
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))

def login_user(email, password):
    try:
        # Attempt Pyrebase authentication
        user = pyre_auth.sign_in_with_email_and_password(email, password)
        account_info = pyre_auth.get_account_info(user['idToken'])
        user_uid = account_info['users'][0]['localId']
        
        # Get user data from Firestore
        user_doc_ref = db.collection("users").document(user_uid)
        user_doc = user_doc_ref.get()
        
        if not user_doc.exists:
            st.error("User record not found in Firestore. Contact admin.")
            return
        
        user_data = user_doc.to_dict()
        is_admin_from_db = user_data.get("is_admin", False)
        username_from_db = user_data.get("username")
        has_set_username_from_db = user_data.get("has_set_username", False)

        if st.session_state["is_admin_attempt"] and not is_admin_from_db:
            st.error("This account does not have admin privileges.")
            return

        st.session_state["logged_in"] = True
        st.session_state["user_email"] = email
        st.session_state["user_uid"] = user_uid
        st.session_state["is_admin"] = is_admin_from_db
        st.session_state["username"] = username_from_db
        st.session_state["has_set_username"] = has_set_username_from_db
        st.session_state["login_mode"] = "logged_in"

        if not has_set_username_from_db:
            st.session_state["needs_username_setup"] = True
            st.success("Please set up your display name and password.")
        else:
            st.session_state["needs_username_setup"] = False
            st.success(f"Welcome, {username_from_db or email}!")
        
        st.rerun()

    except Exception as e:
        st.error(f"Login error: {e}")

def create_user(email, password, is_admin=False):
    try:
        # Create account with Pyrebase
        user = pyre_auth.create_user_with_email_and_password(email, password)
        user_uid = pyre_auth.get_account_info(user['idToken'])['users'][0]['localId']

        # Create Firestore record
        db.collection("users").document(user_uid).set({
            "email": email,
            "is_admin": is_admin,
            "created_at": firestore.SERVER_TIMESTAMP,
            "hashed_password": hash_password(password),
            "username": None,
            "has_set_username": False
        })
        st.success(f"User {email} created successfully.")
        return user_uid
    except Exception as e:
        st.error(f"Error creating user: {e}")
        return None

def logout_user():
    for key in [
        "logged_in", "user_email", "user_uid", "is_admin", "username",
        "has_set_username", "needs_username_setup", "login_success",
        "current_admin_page", "login_mode", "is_admin_attempt"
    ]:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state["login_mode"] = "choose_role"
    st.rerun()

def get_pdf_text(file):
    reader = PdfReader(file)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text

def get_docx_text(file):
    doc = Document(file)
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    return text

def get_openai_response(prompt_text):
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful AI assistant specialized in analyzing Job Descriptions and CVs."
                },
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"OpenAI API error: {e}")
        return "Error generating AI response."
# --- Report Generation ---
def create_comparative_docx_report(jd_text, cv_texts, report_data):
    document = Document()
    document.add_heading(report_data.get("report_title", "JD-CV Comparative Analysis Report"), level=1)
    document.add_paragraph(f"Generated by: {report_data.get('generated_by_username', report_data.get('generated_by_email'))}")
    document.add_paragraph(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    document.add_paragraph(f"Job Description File: {report_data.get('jd_filename', 'N/A')}")
    document.add_paragraph(f"CV Files Analyzed: {', '.join(report_data.get('cv_filenames', ['N/A']))}")
    document.add_page_break()

    # JD analysis
    document.add_heading("Job Description Analysis", level=2)
    jd_prompt = (
        "Analyze the following JD and list key requirements, responsibilities, and qualifications:\n\n"
        f"{jd_text}"
    )
    jd_response = get_openai_response(jd_prompt)
    document.add_paragraph(jd_response)
    document.add_paragraph("\n")

    # Overall CV analysis
    document.add_heading("Overall CV Analysis", level=2)
    cv_summary_prompt = (
        "Given this JD and multiple CVs, provide an overall summary highlighting strengths and gaps:\n\n"
        f"JD:\n{jd_text}\n\nCVs:\n{'---CV---\n'.join(cv_texts)}"
    )
    cv_summary = get_openai_response(cv_summary_prompt)
    document.add_paragraph(cv_summary)
    document.add_paragraph("\n")

    # Individual CV comparison
    document.add_heading("Individual CV Comparison", level=2)
    for i, cv_text in enumerate(cv_texts):
        cv_filename = report_data["cv_filenames"][i] if i < len(report_data["cv_filenames"]) else f"CV {i+1}"
        document.add_heading(f"{cv_filename} Comparison", level=3)
        prompt = (
            "Compare this CV to the JD. Provide:\n"
            "1. Key strengths.\n2. Gaps.\n3. Fit score.\n\n"
            f"JD:\n{jd_text}\n\nCV:\n{cv_text}"
        )
        response = get_openai_response(prompt)
        document.add_paragraph(response)
        if i < len(cv_texts) - 1:
            document.add_page_break()

    # Save to buffer
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer
# --- Main Streamlit App ---
def main():
    # Fixed top-left logo
    try:
        with open("sso_logo.png", "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode()
        st.markdown(
            f'<img src="data:image/png;base64,{logo_b64}" class="fixed-top-left-logo">',
            unsafe_allow_html=True
        )
    except FileNotFoundError:
        st.warning("sso_logo.png not found.")
    except Exception as e:
        st.error(f"Error loading logo: {e}")

    # Login flow
    if not st.session_state["logged_in"]:
        if st.session_state["login_mode"] == "choose_role":
            choose_login_type_page()
        elif st.session_state["login_mode"] == "login_form":
            display_login_page()
        return

    # First-time username/password setup
    if st.session_state.get("needs_username_setup"):
        st.header("Setup Your Account")
        st.warning("Set display name and password.", icon="🔒")
        with st.form("setup_form"):
            new_username = st.text_input("Display Name")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            submit = st.form_submit_button("Save and Re-Login")
            if submit:
                if not new_username:
                    st.warning("Enter display name.")
                elif not new_password:
                    st.warning("Set a password.")
                elif new_password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    try:
                        auth.update_user(st.session_state["user_uid"], password=new_password)
                        db.collection("users").document(st.session_state["user_uid"]).update({
                            "username": new_username,
                            "has_set_username": True,
                            "hashed_password": hash_password(new_password)
                        })
                        st.success("Account updated. Please re-login.")
                        logout_user()
                        return
                    except Exception as e:
                        st.error(f"Error updating account: {e}")
        return

    # Sidebar
    with st.sidebar:
        st.image("sso_logo.png", use_container_width=True)
        st.subheader(f"Welcome, {st.session_state.get('username') or st.session_state['user_email']}")
        st.write(f"Role: {'Admin' if st.session_state['is_admin'] else 'User'}")
        st.write("---")
        if st.session_state["is_admin"]:
            if st.button("Generate Report"):
                st.session_state["current_admin_page"] = "generate"
                st.rerun()
            if st.button("All Reports"):
                st.session_state["current_admin_page"] = "reports"
                st.rerun()
            if st.button("Manage Users"):
                st.session_state["current_admin_page"] = "manage_users"
                st.rerun()
        else:
            if st.button("Generate Report"):
                st.session_state["current_admin_page"] = "generate"
                st.rerun()
            if st.button("All Reports"):
                st.session_state["current_admin_page"] = "reports"
                st.rerun()
        if st.button("Logout"):
            logout_user()

    # Main content
    if st.session_state["current_admin_page"] == "generate":
        generate_comparative_report_page()
    elif st.session_state["current_admin_page"] == "reports":
        show_all_reports_page()
    elif st.session_state["current_admin_page"] == "manage_users":
        manage_users_page()


# --- Footer ---
st.markdown(
    """
    <div style="
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        text-align: center;
        color: #4CAF50;
        padding: 10px;
        background-color: #FFFFFF;
        font-size: 0.8em;
        border-top: 1px solid #E0E0E0;
        z-index: 999;">
        © copyright SSO Consultants
    </div>
    """,
    unsafe_allow_html=True
)

if __name__ == "__main__":
    main()
