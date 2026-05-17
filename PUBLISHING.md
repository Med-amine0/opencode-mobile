# Publishing to Google Play Store

## Required GitHub Secrets

Configure these in **Settings > Secrets and variables > Actions**:

| Secret | Description |
|--------|-------------|
| `PLAY_STORE_SERVICE_ACCOUNT_JSON` | Google Play Console service account JSON key (full JSON content) |
| `KEYSTORE_BASE64` | Base64-encoded release keystore (`base64 -w0 release.keystore`) |
| `KEYSTORE_PASSWORD` | Keystore password |
| `KEY_ALIAS` | Key alias in the keystore |
| `KEY_PASSWORD` | Key password |

## Setup Steps

### 1. Create a release keystore

```bash
keytool -genkeypair -v -storetype PKCS12 \
  -keystore release.keystore -alias release \
  -keyalg RSA -keysize 2048 -validity 10000
```

Encode it for GitHub secrets:
```bash
base64 -w0 release.keystore
```

### 2. Create a Google Play service account

1. Go to [Google Cloud Console](https://console.cloud.google.com/) > IAM > Service Accounts
2. Create a service account and download the JSON key
3. In Google Play Console > Settings > API access, link the service account
4. Grant it release management permissions for your app

### 3. Workflow triggers

The publish workflow runs on:
- GitHub Release publish events
- Tag pushes matching `v*`

It builds an AAB (Android App Bundle), signs it with the release keystore, and uploads to the **internal** track. Promote to production via Play Console.

## Fastlane (Alternative)

A Fastlane setup is included for local publishing:

```bash
bundle install
bundle exec fastlane android deploy
```

Set environment variables: `SUPPLY_JSON_KEY`, `RELEASE_STORE_FILE`, `RELEASE_STORE_PASSWORD`, `RELEASE_KEY_ALIAS`, `RELEASE_KEY_PASSWORD`.
