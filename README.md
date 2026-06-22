# Dealer Scraper

A plugin-based Python scraper with a Streamlit interface, Excel exports, and an
optional private Amazon S3 file dashboard.

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run streamlit_app.py
```

Set `BUCKET_NAME` in `.env` to a globally unique S3 bucket name. Use a rotated
AWS key, an AWS profile, or an IAM role; never commit credentials. The app can
create the configured bucket from the **S3 Files** tab if the active AWS identity
has `s3:CreateBucket` permission.

Generated exports use the private `exports/` prefix. New buckets have public
access blocked and AES-256 server-side encryption enabled. `S3_PUBLIC_BUCKET` is
reserved for separate public assets and is not used for dealer exports.

## AWS permissions

The application identity needs `s3:ListBucket`, `s3:GetObject`, `s3:PutObject`,
and `s3:DeleteObject`. Bucket creation additionally needs `s3:CreateBucket`,
`s3:PutBucketPublicAccessBlock`, and `s3:PutEncryptionConfiguration`.

## CLI

```powershell
python main.py --list-brands
python main.py --brand "GM Modular" --category "High Efficient Fans" --state Karnataka --city Bengaluru
```

