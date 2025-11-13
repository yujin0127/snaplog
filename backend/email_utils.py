"""
이메일 발송 유틸리티
Gmail SMTP 또는 SendGrid 사용
"""
import os
import smtplib
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# 이메일 설정
EMAIL_METHOD = os.getenv("EMAIL_METHOD", "gmail")  # "gmail" 또는 "sendgrid"
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")  # Gmail 앱 비밀번호
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", GMAIL_ADDRESS)
APP_NAME = "스냅로그"
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:5000")

def generate_verification_code(length=6) -> str:
    """6자리 인증 코드 생성"""
    return ''.join(random.choices(string.digits, k=length))

def generate_reset_token(length=32) -> str:
    """비밀번호 재설정 토큰 생성"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def send_email_via_gmail(to_email: str, subject: str, html_body: str) -> bool:
    """Gmail SMTP로 이메일 발송"""
    try:
        if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
            print("❌ Gmail 설정 누락: GMAIL_ADDRESS, GMAIL_APP_PASSWORD")
            return False
        
        msg = MIMEMultipart('alternative')
        msg['From'] = FROM_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        
        html_part = MIMEText(html_body, 'html', 'utf-8')
        msg.attach(html_part)
        
        # Gmail SMTP 서버 연결
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        
        print(f"✅ 이메일 발송 성공: {to_email}")
        return True
        
    except Exception as e:
        print(f"❌ Gmail 이메일 발송 실패: {e}")
        return False

def send_email_via_sendgrid(to_email: str, subject: str, html_body: str) -> bool:
    """SendGrid API로 이메일 발송"""
    try:
        if not SENDGRID_API_KEY:
            print("❌ SendGrid API 키 누락")
            return False
        
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        
        message = Mail(
            from_email=FROM_EMAIL,
            to_emails=to_email,
            subject=subject,
            html_content=html_body
        )
        
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        
        print(f"✅ SendGrid 이메일 발송 성공: {to_email}, status={response.status_code}")
        return True
        
    except Exception as e:
        print(f"❌ SendGrid 이메일 발송 실패: {e}")
        return False

def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """이메일 발송 (설정에 따라 Gmail 또는 SendGrid 사용)"""
    if EMAIL_METHOD == "sendgrid":
        return send_email_via_sendgrid(to_email, subject, html_body)
    else:
        return send_email_via_gmail(to_email, subject, html_body)

# ============ 이메일 템플릿 ============

def send_verification_email(to_email: str, code: str) -> bool:
    """회원가입 인증 이메일 발송"""
    subject = f"[{APP_NAME}] 회원가입 인증 코드"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #333;">회원가입 인증</h2>
        <p>안녕하세요! {APP_NAME}에 가입해 주셔서 감사합니다.</p>
        <p>아래 인증 코드를 입력하여 회원가입을 완료해주세요:</p>
        
        <div style="background-color: #f5f5f5; padding: 20px; text-align: center; margin: 20px 0; border-radius: 8px;">
            <h1 style="color: #4CAF50; margin: 0; letter-spacing: 5px;">{code}</h1>
        </div>
        
        <p style="color: #666; font-size: 14px;">
            이 인증 코드는 10분간 유효합니다.<br>
            본인이 요청하지 않았다면 이 이메일을 무시하세요.
        </p>
        
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="color: #999; font-size: 12px;">
            {APP_NAME} 팀<br>
            <a href="{APP_URL}" style="color: #4CAF50;">{APP_URL}</a>
        </p>
    </div>
    """
    
    return send_email(to_email, subject, html_body)

def send_password_reset_email(to_email: str, reset_token: str) -> bool:
    """비밀번호 재설정 이메일 발송"""
    reset_url = f"{APP_URL}/reset-password?token={reset_token}"
    
    subject = f"[{APP_NAME}] 비밀번호 재설정"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #333;">비밀번호 재설정</h2>
        <p>안녕하세요!</p>
        <p>비밀번호 재설정 요청을 받았습니다. 아래 버튼을 클릭하여 새 비밀번호를 설정해주세요:</p>
        
        <div style="text-align: center; margin: 30px 0;">
            <a href="{reset_url}" 
               style="background-color: #4CAF50; color: white; padding: 12px 30px; 
                      text-decoration: none; border-radius: 5px; display: inline-block;">
                비밀번호 재설정하기
            </a>
        </div>
        
        <p style="color: #666; font-size: 14px;">
            또는 아래 링크를 복사하여 브라우저에 붙여넣으세요:<br>
            <a href="{reset_url}" style="color: #4CAF50; word-break: break-all;">{reset_url}</a>
        </p>
        
        <p style="color: #666; font-size: 14px;">
            이 링크는 1시간 동안 유효합니다.<br>
            본인이 요청하지 않았다면 이 이메일을 무시하세요.
        </p>
        
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="color: #999; font-size: 12px;">
            {APP_NAME} 팀<br>
            <a href="{APP_URL}" style="color: #4CAF50;">{APP_URL}</a>
        </p>
    </div>
    """
    
    return send_email(to_email, subject, html_body)