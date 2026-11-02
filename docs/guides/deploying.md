# Deploying

The AWS side is created with Terraform from a workstation; the weeknight job runs on an Oracle Cloud Always Free VM. The reasoning behind the split is in the [cloud architecture](../architectures/cloud-lakehouse.md).

## 1. AWS

Use an admin IAM user rather than the root account. With `aws login` the CLI gets short-lived credentials, so sign in again if Terraform reports missing credentials:

```sh
aws login --profile <profile>
export AWS_PROFILE=<profile>
aws sts get-caller-identity          # should show the admin user
```

Then create the resources:

```sh
cd infra/aws
cp terraform.tfvars.example terraform.tfvars   # bucket name (globally unique) and alarm email
terraform init
terraform plan                                 # 20 resources; check lake_uri shows your bucket
terraform apply
```

Terraform creates two IAM users with their own access keys: `sec-edgar-pipeline` for the VM and `sec-edgar-dashboard` for Streamlit. Read them with `terraform output -raw <name>`. The state file holds those keys, so `*.tfstate` and `terraform.tfvars` are ignored by git; `.terraform.lock.hcl` is committed to pin the provider version.

The budget alarm covers the whole AWS account. If the account runs other workloads, raise `monthly_budget_usd` accordingly.

## 2. The VM

The job is sized for an Ampere A1 with 2 OCPUs and 12 GB, the full Always Free allowance. It runs in its own container capped at 1 CPU and 5 GB, so it can share the VM with other workloads. On Ubuntu, install Docker first; `buildx` is a separate package:

```sh
sudo apt update
sudo apt install -y docker.io docker-buildx
sudo systemctl enable --now docker
```

Then the project:

```sh
git clone https://github.com/dhirajkk91/sec-edgar-financial-data-lakehouse.git
cd sec-edgar-financial-data-lakehouse
sudo sh deploy/pipeline/install.sh   # first run: creates /etc/sec-edgar/env and stops
sudo nano /etc/sec-edgar/env         # SEC_USER_AGENT, lake URI, the pipeline key
sudo sh deploy/pipeline/install.sh   # builds the image and enables the timer
```

`systemctl list-timers sec-edgar-nightly.timer` shows the next run in UTC; 11:30 p.m. Central is 05:30 UTC the following day. To change the CPU or memory cap without editing the repository, run `sudo systemctl edit sec-edgar-nightly.service` and set `SEC_EDGAR_CPUS` or `SEC_EDGAR_MEMORY`.

## 3. Test with one company

Run one company through sync and publish before starting the backfill:

```sh
sudo docker run --rm --cpus 1 --memory 5g --env-file /etc/sec-edgar/env \
  -v /var/lib/sec-edgar:/data sec-edgar-pipeline sh -c \
  "sec-edgar sync --universe config/universe-sp500.json --data-root /data/lake --ciks 0000320193 \
   && sec-edgar publish-lake --data-root /data/lake"
```

Expected output for Apple:

```text
  1/1 Apple Inc.: pending=23 done_now=23
Sync COMPLETE: 1 companies, 23 new filings done, 0 failed, 0 company errors, Gold COMPLETE in 219s
Lake s3://<bucket>/sec-edgar: 23 Bronze filings, 31,198 Silver rows, 325 Gold rows, snapshot …
```

Verify in Athena (workgroup `sec-edgar`, catalog `AwsDataCatalog`, database `sec_gold`):

```sql
SELECT form, count(*) FROM sec_gold.dim_filings GROUP BY form;   -- 10-K: 5, 10-Q: 18
```

## 4. The backfill

```sh
sudo systemctl start --no-block sec-edgar-nightly.service
journalctl -u sec-edgar-nightly.service -f
```

The job logs one line per company. It can be stopped safely; the next start resumes from the last finished company, and systemd won't start a second instance if the timer fires while it's running. The run summary is written to `/var/lib/sec-edgar/lake/sync-runs/latest.json` on completion.

## 5. Streamlit

Add the snapshot location and the dashboard key (not the pipeline key) to the app's secrets:

```toml
SEC_EDGAR_SNAPSHOT_S3_URI = 's3://<bucket>/sec-edgar/serving/sec_edgar_gold.duckdb'
AWS_ACCESS_KEY_ID = 'AKIA...'
AWS_SECRET_ACCESS_KEY = '...'
AWS_DEFAULT_REGION = 'us-east-1'
```

Each value needs exactly one pair of straight quotes. `terraform output` without `-raw` adds its own quotes, which makes the TOML invalid. Single quotes are safest because AWS secrets can contain `+` and `/`. Without these secrets, or if the download fails, the app shows the committed 20-company demo.

## Updating

```sh
git pull && sudo sh deploy/pipeline/install.sh
```
