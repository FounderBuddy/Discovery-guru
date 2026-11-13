#!/usr/bin/env bash
# Deploys Discovery Guru to a GCP sandbox project on Cloud Run.
#
#   PROJECT_ID=my-sandbox REGION=europe-west2 ./deploy/cloudrun/deploy.sh
#
# What it creates (idempotent where gcloud allows):
#   - Artifact Registry repo "sdp", images built with Cloud Build
#   - Cloud SQL Postgres 16 instance + "ledger" database, password in Secret Manager
#   - one service account per service (least privilege; each may invoke only what it calls)
#   - Direct VPC egress + Cloud NAT on the default network (needed for internal ingress)
#   - 12 Cloud Run services: every one --no-allow-unauthenticated; all but api-gateway ingress=internal
#   - IAP in front of api-gateway (the only door for people)
#   - Cloud Scheduler job that ages open items daily via engine-resolution /v1/tick
# Model access: Claude on Vertex AI via the model-gateway service account (roles/aiplatform.user).
# Check the flags marked VERIFY against current gcloud docs before the first run.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-europe-west2}"
REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/sdp"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
SQL_INSTANCE="${SQL_INSTANCE:-sdp-ledger}"
IAP_MEMBERS="${IAP_MEMBERS:?set IAP_MEMBERS, e.g. group:presales@yourco.com}"
MODEL_DEFAULT="${MODEL_DEFAULT:-claude-vertex}"

SERVICES=(ledger event-bus model-gateway ingestion engine-extraction engine-divergence
          engine-questions engine-recommendation engine-resolution engine-artefacts engine-brief api-gateway)

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "== APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  sqladmin.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com \
  iap.googleapis.com cloudscheduler.googleapis.com

echo "== Artifact Registry"
gcloud artifacts repositories describe sdp --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create sdp --repository-format docker --location "$REGION"

echo "== Build images"
gcloud builds submit --config deploy/cloudrun/cloudbuild.yaml \
  --substitutions "_REPO=${REPO},_TAG=${TAG}" .

echo "== Cloud SQL"
if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" --database-version POSTGRES_16 --region "$REGION" \
    --edition ENTERPRISE --tier db-g1-small --storage-auto-increase
  gcloud sql databases create ledger --instance "$SQL_INSTANCE"
  PW="$(openssl rand -base64 30 | tr -d '/+=')"
  gcloud sql users create sdp --instance "$SQL_INSTANCE" --password "$PW"
  printf '%s' "$PW" | gcloud secrets create sdp-ledger-db-password --data-file=-
fi
SQL_CONN="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"

echo "== Network: internal-ingress services only accept calls that arrive through a VPC,
#    so every service egresses via Direct VPC; Cloud NAT keeps outbound internet (external model APIs) working."
NETWORK="${NETWORK:-default}"; SUBNET="${SUBNET:-default}"
gcloud services enable compute.googleapis.com
gcloud compute networks subnets update "$SUBNET" --region "$REGION" --enable-private-ip-google-access
gcloud compute routers describe sdp-router --region "$REGION" >/dev/null 2>&1 || \
  gcloud compute routers create sdp-router --network "$NETWORK" --region "$REGION"
gcloud compute routers nats describe sdp-nat --router sdp-router --region "$REGION" >/dev/null 2>&1 || \
  gcloud compute routers nats create sdp-nat --router sdp-router --region "$REGION" \
    --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges

echo "== Service accounts"
sa() { echo "sdp-${1}@${PROJECT_ID}.iam.gserviceaccount.com" | sed 's/engine-/e-/'; }
SA_MAP="{"
for s in "${SERVICES[@]}"; do
  id="sdp-${s/engine-/e-}"
  gcloud iam service-accounts describe "$(sa "$s")" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$id" --display-name "Discovery Guru ${s}"
  SA_MAP+="\"$(sa "$s")\":\"${s}\","
done
SA_MAP="${SA_MAP%,}}"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$(sa ledger)" \
  --role roles/cloudsql.client --condition None >/dev/null
gcloud secrets add-iam-policy-binding sdp-ledger-db-password --member "serviceAccount:$(sa ledger)" \
  --role roles/secretmanager.secretAccessor >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$(sa model-gateway)" \
  --role roles/aiplatform.user --condition None >/dev/null

url() { echo "https://${1}-${PROJECT_NUMBER}.${REGION}.run.app"; }   # deterministic Cloud Run URLs

COMMON="INTERNAL_AUTH=gcp,BUS_BACKEND=http,GCP_PROJECT=${PROJECT_ID}"
COMMON+=",LEDGER_URL=$(url ledger),EVENT_BUS_URL=$(url event-bus),MODEL_GATEWAY_URL=$(url model-gateway)"
COMMON+=",INGESTION_URL=$(url ingestion),ENGINE_EXTRACTION_URL=$(url engine-extraction)"
COMMON+=",ENGINE_DIVERGENCE_URL=$(url engine-divergence),ENGINE_QUESTIONS_URL=$(url engine-questions)"
COMMON+=",ENGINE_RECOMMENDATION_URL=$(url engine-recommendation),ENGINE_RESOLUTION_URL=$(url engine-resolution)"
COMMON+=",ENGINE_ARTEFACTS_URL=$(url engine-artefacts),ENGINE_BRIEF_URL=$(url engine-brief)"

ENVFILE="$(mktemp)"; trap 'rm -f "$ENVFILE"' EXIT

deploy() {  # deploy <service> <ingress> <extra env as YAML lines, may be ""> [extra gcloud args...]
  local s="$1" ingress="$2" extra="$3"; shift 3
  local image="${REPO}/${s}:${TAG}"
  { echo "SDP_SERVICE_ACCOUNTS: '${SA_MAP}'"
    echo "${COMMON}" | tr ',' '\n' | sed 's/=/: "/; s/$/"/'
    if [[ -n "$extra" ]]; then printf "%s\n" "$extra"; fi
  } > "$ENVFILE"
  gcloud run deploy "$s" --image "$image" --region "$REGION" --service-account "$(sa "$s")" \
    --no-allow-unauthenticated --ingress "$ingress" --env-vars-file "$ENVFILE" \
    --network "$NETWORK" --subnet "$SUBNET" --vpc-egress all-traffic \
    --cpu 1 --memory 512Mi --min-instances 0 --max-instances 3 "$@"
}

echo "== Deploy services"
deploy ledger internal \
  "LEDGER_DB_URL: 'postgresql+psycopg://sdp:{password}@/ledger?host=/cloudsql/${SQL_CONN}'" \
  --add-cloudsql-instances "$SQL_CONN" --update-secrets LEDGER_DB_PASSWORD=sdp-ledger-db-password:latest
deploy event-bus internal \
  "SDP_SUBSCRIPTIONS: '{\"sdp.source.added.v1\":[\"$(url engine-extraction)/v1/events\"]}'"
deploy model-gateway internal "SDP_MODEL_DEFAULT: \"${MODEL_DEFAULT}\"" --memory 1Gi --timeout 600
deploy ingestion internal "" --memory 1Gi
for e in extraction divergence questions recommendation resolution artefacts brief; do
  deploy "engine-${e}" internal "" --timeout 900
done
# VERIFY: direct IAP on Cloud Run is a beta flag at the time of writing.
deploy api-gateway all "AUTH_MODE: \"iap\"
IAP_AUDIENCE: \"/projects/${PROJECT_NUMBER}/locations/${REGION}/services/api-gateway\""
gcloud beta run services update api-gateway --region "$REGION" --iap
for m in ${IAP_MEMBERS//,/ }; do
  gcloud beta iap web add-iam-policy-binding --resource-type cloud-run --service api-gateway \
    --region "$REGION" --member "$m" --role roles/iap.httpsResourceAccessor >/dev/null
done
gcloud run services add-iam-policy-binding api-gateway --region "$REGION" \
  --member "serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" \
  --role roles/run.invoker >/dev/null

echo "== Who may call whom (Cloud Run invoker = the network-level half of SERVICE_PERMS)"
grant() { gcloud run services add-iam-policy-binding "$1" --region "$REGION" \
  --member "serviceAccount:$(sa "$2")" --role roles/run.invoker >/dev/null; }
for caller in "${SERVICES[@]}"; do
  [[ "$caller" == ledger ]] || grant ledger "$caller"
  [[ "$caller" == ledger || "$caller" == event-bus ]] || grant event-bus "$caller"
done
for caller in engine-extraction engine-divergence engine-questions engine-recommendation engine-brief; do grant model-gateway "$caller"; done
for e in ingestion engine-extraction engine-divergence engine-questions engine-recommendation \
         engine-resolution engine-artefacts engine-brief model-gateway; do grant "$e" api-gateway; done
grant engine-extraction event-bus   # AUTO_EXTRACT push delivery

echo "== Scheduler: daily ageing of open items"
gcloud iam service-accounts describe "sdp-scheduler@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
  gcloud iam service-accounts create sdp-scheduler --display-name "Discovery Guru scheduler"
gcloud run services add-iam-policy-binding engine-resolution --region "$REGION" \
  --member "serviceAccount:sdp-scheduler@${PROJECT_ID}.iam.gserviceaccount.com" --role roles/run.invoker >/dev/null
gcloud scheduler jobs describe sdp-ageing --location "$REGION" >/dev/null 2>&1 || \
  gcloud scheduler jobs create http sdp-ageing --location "$REGION" --schedule "17 6 * * 1-5" \
    --http-method POST --uri "$(url engine-resolution)/v1/tick" \
    --oidc-service-account-email "sdp-scheduler@${PROJECT_ID}.iam.gserviceaccount.com" \
    --oidc-token-audience "$(url engine-resolution)"
echo "  (add sdp-scheduler@${PROJECT_ID}.iam.gserviceaccount.com -> engine-resolution to SDP_SERVICE_ACCOUNTS"
echo "   and to SERVICE_PERMS if /v1/tick should accept it)"

echo
echo "Done. Open $(url api-gateway) (IAP will ask you to sign in with Google)."
