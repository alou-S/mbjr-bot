import requests
import config


def send_otp(otp, netid):
    url = "https://api.useplunk.com/v1/send"

    email_body = f"""<body style="font-family: 'Arial', sans-serif; background-color: #1a1a1a; color: #e0e0e0; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
        <div style="background-color: #2a2a2a; border-radius: 10px; padding: 30px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
            <h1 style="color: #4a90e2; margin-bottom: 20px; text-align: center;">Verification Code</h1>
            <p style="color: #e0e0e0; text-align: center; font-size: 16px;">Here's your 6-digit OTP for mbjr-bot:</p>
            <div style="background: linear-gradient(45deg, #4a90e2, #63b3ed); border-radius: 8px; padding: 20px; text-align: center; font-size: 32px; font-weight: bold; letter-spacing: 8px; margin: 30px 0; color: #ffffff;">
                {otp}
            </div>
            <p style="text-align: center; font-size: 14px; color: #b0b0b0;">This code will expire in 5 minutes.</p>
            <hr style="border: none; border-top: 1px solid #3a3a3a; margin: 30px 0;">
            <p style="text-align: center; font-size: 14px; color: #b0b0b0;">If you didn't request this code, please ignore this email.</p>
        </div>
        <footer style="text-align: center; margin-top: 30px; font-size: 12px; color: #707070;">
            This is an automated message. Please do not reply.
        </footer>
    </body>
    """
    payload = {
        "to": f"{netid}@srmist.edu.in",
        "subject": "mbjr-part",
        "body": email_body,
        "subscribed": False,
        "name": "mbjr-bot",
        "from": "otp@potat.cc",
        "reply": "no-reply@potat.cc",
        "headers": {},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.PLUNK_TOKEN}",
    }

    response = requests.request("POST", url, json=payload, headers=headers)

    return response
