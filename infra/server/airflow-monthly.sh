#!/usr/bin/env bash
# Monthly pipeline on a sleeping Airflow (scale-to-zero, ADR-0010).
# Started by systemd (taxi-airflow-monthly.timer). Wakes Airflow, runs the monthly
# refresh (TLC month -> Databricks -> gold snapshot), waits for the training run its
# asset event triggers, then puts Airflow back to sleep, whatever happened.
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/apps/taxi-mlops}   # pinned checkout of this repo
COMPOSE=(docker compose -f "$APP_DIR/orchestration/airflow/compose.yaml"
         -f "$APP_DIR/infra/server/compose.airflow.yaml")
MAX_WAIT_MINUTES=${MAX_WAIT_MINUTES:-180}

log() { echo "$(date -u +%FT%TZ) $*"; }
api() { "${COMPOSE[@]}" exec -T airflow-apiserver curl -fsS "http://127.0.0.1:8080/api/v2/$1"; }

sleep_airflow() { log "stopping Airflow"; "${COMPOSE[@]}" stop >/dev/null; }
trap sleep_airflow EXIT

log "waking Airflow"
"${COMPOSE[@]}" up -d --no-build --wait >/dev/null

"${COMPOSE[@]}" exec -T airflow-scheduler airflow dags unpause taxi_monthly_refresh >/dev/null
"${COMPOSE[@]}" exec -T airflow-scheduler airflow dags unpause taxi_train_and_promote >/dev/null
log "triggering taxi_monthly_refresh (month = two months before today)"
"${COMPOSE[@]}" exec -T airflow-scheduler airflow dags trigger taxi_monthly_refresh >/dev/null

state_of() {  # newest run state of a DAG
  api "dags/$1/dagRuns?order_by=-run_after&limit=1" \
    | python3 -c "import json,sys; r=json.load(sys.stdin)['dag_runs']; print(r[0]['state'] if r else 'none')"
}

deadline=$(( $(date +%s) + MAX_WAIT_MINUTES * 60 ))
for dag in taxi_monthly_refresh taxi_train_and_promote; do
  log "waiting for $dag"
  sleep 60  # give the scheduler (and the asset trigger) time to create the run
  while :; do
    state=$(state_of "$dag" || echo unknown)
    case "$state" in
      success) log "$dag: success"; break ;;
      failed)  log "$dag: FAILED"; exit 1 ;;
    esac
    if (( $(date +%s) > deadline )); then log "timed out waiting for $dag ($state)"; exit 1; fi
    sleep 60
  done
done
log "monthly pipeline finished"
