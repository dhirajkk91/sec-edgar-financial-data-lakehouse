# Deploying it

These are the steps I use to run the pipeline in the cloud: the AWS side from my laptop with Terraform, and the weeknight job on my Oracle Cloud free-tier VM (the same one that runs my Bluesky project). Why it's split this way is in the [cloud notes](../architectures/cloud-lakehouse.md).

I haven't done the full S&P 500 backfill yet, so I'll add real timings, costs and whatever breaks once I have.

## AWS

```sh
cd infra/aws
cp terraform.tfvars.example terraform.tfvars   # bucket name and the email for the budget alarm
terraform init
terraform apply
```

AWS sends an email to confirm the budget alarm; it doesn't fire until you click it. The outputs have everything the next two steps need (`terraform output`, and `-raw` for the secret keys). The state file holds those keys, which is why `*.tfstate` is in `.gitignore`.

## The VM

```sh
git clone https://github.com/dhirajkk91/sec-edgar-financial-data-lakehouse.git
cd sec-edgar-financial-data-lakehouse
sudo sh deploy/pipeline/install.sh   # first time: creates /etc/sec-edgar/env and stops
sudo nano /etc/sec-edgar/env         # SEC_USER_AGENT, the lake URI, the pipeline key
sudo sh deploy/pipeline/install.sh   # builds the image and turns on the timer
```

Before the full run I check one company with `--dry-run`, which only lists what it would download:

```sh
sudo docker run --rm --env-file /etc/sec-edgar/env -v /var/lib/sec-edgar:/data \
  sec-edgar-pipeline sec-edgar sync --universe config/universe-sp500.json \
  --data-root /data/lake --ciks 0000320193 --dry-run
```

Then the first real run, which is the five-year backfill:

```sh
sudo systemctl start --no-block sec-edgar-nightly.service
journalctl -u sec-edgar-nightly.service -f
```

It's safe to stop; the next start picks up from the last company it finished. After that the timer runs it Monday to Friday at 11:30 p.m. Central, and `git pull && sudo sh deploy/pipeline/install.sh` updates it.

## Dashboard

The Streamlit app reads the nightly snapshot once these are in its secrets (use the dashboard key, which can only read the snapshot, not the pipeline key):

```toml
SEC_EDGAR_SNAPSHOT_S3_URI = "s3://<bucket>/sec-edgar/serving/sec_edgar_gold.duckdb"
AWS_ACCESS_KEY_ID = "..."
AWS_SECRET_ACCESS_KEY = "..."
AWS_DEFAULT_REGION = "us-east-1"
```

Without them it shows the 20-company demo, as it always has.

## Athena

The `sec-edgar` workgroup has my saved queries from [`infra/aws/queries/`](../../infra/aws/queries/). It cancels anything that would scan more than 1 GiB, so a bad query costs half a cent at most.
