#!/usr/bin/env bash
# Upload the scored graph to the deployed private bucket.
# Usage: scripts/upload_graph.sh <bucket-name> [key] [file]
set -euo pipefail

BUCKET="${1:?usage: upload_graph.sh <bucket> [key] [file]}"
KEY="${2:-graph.json.gz}"
FILE="${3:-data/graph.json.gz}"

aws s3 cp "$FILE" "s3://${BUCKET}/${KEY}" --content-encoding gzip
echo "Uploaded ${FILE} -> s3://${BUCKET}/${KEY}"
