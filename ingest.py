# pip install --upgrade google-auth google-auth-oauthlib google-api-python-client
import argparse
import base64
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import json
import os
import time
import re

STORAGE_PATH="output"
DEFAULT_LABELS = [
    "CHAT",
    "SENT",
    "INBOX",
    "IMPORTANT",
    "TRASH",
    "DRAFT",
    "SPAM",
    "CATEGORY_FORUMS",
    "CATEGORY_UPDATES",
    "CATEGORY_PERSONAL",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_SOCIAL",
    "STARRED",
    "UNREAD",
]


# load saved token (contains refresh_token)
creds = Credentials.from_authorized_user_file('token.json', scopes=['https://www.googleapis.com/auth/gmail.readonly'])
service = build('gmail', 'v1', credentials=creds)


def list_labels(service):
    labels = get_labels(service)
    
    if not labels:
      print("No labels found.")
      return
    print("Labels:")
    for label in labels:
      print(label["name"])
    

def list_messages(service, recency="1d"):
    # TODO: Validate recency

    # list messages (modify query as needed)
    results = service.users().messages().list(userId='me', q=f'newer_than:{recency}').execute()
    msgs = results.get('messages', [])
    for m in msgs:
        msg = service.users().messages().get(userId='me', id=m['id'], format='full').execute()
            # parse payload / headers / body; then ingest


def get_labels(service, nondefault_only=True):
    results = service.users().labels().list(userId="me").execute()
    labels = results.get("labels", [])

    if nondefault_only:
        return [l for l in labels if l['name'] not in DEFAULT_LABELS]
    else:
        return labels


def latest_unread_message(service, label="UNREAD", user_id='me', max_results=1):
    """
    Workhorse for ingesting from a label
    - userId: 'me'
    - q: 'is:unread label:{label}'
    - maxResults: 1
    - includeSpamTrash: False
    """
    query_string = f'is:unread label:{label}'

    try:
        results = service.users().messages().list(userId=user_id, q=query_string, 
                              maxResults=max_results, includeSpamTrash=False).execute() 
        messages = results.get('messages', [])
        if not messages:
            print(f"No unread messages found for label: {label}")
            return None

        # 2. Extract the message ID
        message_id = messages[0]['id']

        # 3. Get the full message details
        message = service.users().messages().get(
            userId=user_id,
            id=message_id,
            format='full' # or 'raw' or 'metadata' depending on what you need
        ).execute()
        
        return message

    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def decode_b64url(b64_data, charset="utf-8"):
    """
    Decode URL-safe base64 (A-Za-z0-9-_), handling missing padding and newlines.
    Returns (decoded_bytes, decoded_text).
    """
    # normalize to bytes
    if isinstance(b64_data, str):
        b = b64_data.encode("ascii")
    else:
        b = b64_data

    # remove whitespace/newlines that might be in the string
    b = re.sub(rb"\s+", b"", b)

    # add padding if necessary
    padding = (-len(b)) % 4
    if padding:
        b += b"=" * padding

    decoded_bytes = base64.urlsafe_b64decode(b)
    decoded_text = decoded_bytes.decode(charset, errors="replace")
    return decoded_text


def ingest(label, count_emails):

    emails = []
    for i in range(count_emails):
            print(f"Getting {i}th latest unread message for specified label...") 
            message = latest_unread_message(service, label=label)

            print(f"Message preview:\n{str(message['payload'])[:300]}")        
            body = [] 
            for part in message['payload']['parts']:
                body_b64 = part['body']['data']
                bodypart = decode_b64url(body_b64) + "\n\n\n\nXXXXX\n\n\n\n"
                body.append(bodypart)

            email = {'body': body, 'raw': message, 'label': label}
            emails.append(email)
    return emails


def dump_emails(emails):
    for email in emails:
        prefix = email['label'].replace(' ', '_')
        # subj = email['raw']['payload']['parts'][0]['subject'].replace(' ', '_')
        # fname = f"{prefix}_{subj}_{tstamp}.eml"
        # with open(os.path.join(STORAGE_PATH, fname), "w") as f:
        #     for part in body: 
        #         f.write(part)

        processing_tstamp = round(time.time())
        dump_name = f"{prefix}_{processing_tstamp}.pkl"
        import pickle
        fpath = os.path.join(STORAGE_PATH, dump_name)

        with open(fpath, 'wb') as f:
            pickle.dump(email['raw'], f)

        

            

# ====================================================================
#                               Output
# ====================================================================


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--label", dest="label", help="Ingest message(s) from label")
    parser.add_argument("-mu", "--max-unread", dest="max_unread", help="Max number of unread messages to pull from label") 
    parser.add_argument("-w", "--write-file", dest="write", action="store_true", help="Write the email body to a time-stamped file")
    args = parser.parse_args()
    
    if not args.label:
        print(f"No label specified; printing available labels.")
        list_labels(service)

    else:
        valid_mu = args.max_unread and args.max_unread.isdigit() and int(args.max_unread) > 0
        count_emails = int(args.max_unread) if valid_mu else 1

        emails = ingest(args.label, count_emails)

        if args.write:
            dump_emails(emails)
    return emails

        

if __name__=="__main__":
    emails = main()
