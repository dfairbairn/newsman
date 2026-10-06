print(f"0. Loading")
# create_token.py
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
import json

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']  # adjust scope as needed

print(f"1.")
# 1) Make sure you have client_secret.json downloaded from Google Cloud Console
flow = InstalledAppFlow.from_client_secrets_file('client3_secret.json', SCOPES)

print(f"2.")
# This opens a browser and completes the OAuth flow interactively.
creds = flow.run_local_server(port=0)

print(f"3.")
# Save the authorized user credentials (this file is what from_authorized_user_file expects)
with open('token.json', 'w') as f:
    f.write(creds.to_json())

    print("Saved token.json — inspect it to confirm it contains a refresh_token.")

