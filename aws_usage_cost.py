import calendar
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import requests


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.handlers = [handler]

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
HEALTHCHECKS_URL = os.environ["HEALTHCHECKS_URL_AWS_USAGE_COST"]
THRESHOLD = float(os.environ["THRESHOLD"])
N8N_WEBHOOK_URL = os.environ["N8N_WEBHOOK_URL"]
N8N_CREDENTIALS = os.environ["N8N_CREDENTIALS"]
AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]


def get_current_costs() -> float:
    """Get the current month's AWS costs."""

    client = boto3.client(
        "ce",
        "us-east-1",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )  # AWS Cost Explorer client

    # Get the current date and the first day of the month
    end = datetime.now(timezone.utc).date()
    start = datetime(end.year, end.month, 1).date()

    # Ensure start date is before end date or it will throw an exception
    if start >= end:
        end = end + timedelta(days=1)

    try:
        # Retrieve the cost and usage data
        response = client.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["BlendedCost"],
        )
    except Exception as exception:
        logger.exception(f"Failed to retrieve AWS costs. Exception: {exception}")
        return 0.0

    return response["ResultsByTime"][0]["Total"]["BlendedCost"]["Amount"]


def get_end_of_month_projection(current_cost: float) -> tuple[float, float]:
    """Get the projected end-of-month AWS costs and spending."""
    current_date = datetime.now().date()
    days_in_month = calendar.monthrange(current_date.year, current_date.month)[1]
    current_day_of_month = current_date.day
    remaining_days = days_in_month - current_day_of_month
    projected_cost = (current_cost / current_date.day) * days_in_month
    projected_spending = (current_cost / current_date.day) * remaining_days

    return projected_cost, projected_spending


def send_n8n_webhook(message) -> dict:
    """Send a notification to n8n webhook."""
    payload = {"title": "AWS Usage Cost", "message": message}
    try:
        response = requests.post(
            N8N_WEBHOOK_URL,
            headers={
                "Authorization": f"Basic {N8N_CREDENTIALS}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        logger.info(
            f"n8n webhook sent successfully. Status code: {response.status_code}"
        )
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.exception(f"Failed to send n8n webhook. Exception: {exception}")
        return {"ok": False, "status": "Failed"}


def send_discord_notification(message) -> dict:
    """Send a notification using Discord."""
    payload = {"content": message}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL, json=payload, headers=headers, timeout=5
        )
        response.raise_for_status()
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.exception(f"Failed to send Discord notification. Exception: {exception}")
        return {"ok": False, "status": "Failed"}


def send_cost_notifications(current_cost, projected_cost, projected_spending):
    """Send notifications to n8n (always) and Discord (only if threshold exceeded)."""
    now = datetime.now().isoformat()
    summary = (
        f"Current month: {current_cost} USD\n"
        f"Projected end-of-month: {projected_cost:.2f} USD\n"
        f"Projected spending: {projected_spending:.2f} USD"
    )
    # Always send to n8n webhook
    n8n_response = send_n8n_webhook(summary)
    logger.info({"msg": "n8n webhook sent", "response": n8n_response})

    # Only send to Discord if threshold exceeded
    if projected_cost > THRESHOLD:
        discord_msg = f"ATTENTION! Projected end-of-month AWS costs of {projected_cost:.2f} USD exceeds {THRESHOLD} USD!\n\n{summary}"
        discord_response = send_discord_notification(discord_msg)
        logger.info(
            {"msg": "Discord notification attempted", "response": discord_response}
        )
        return discord_response["ok"]
    return None


def main():
    """Run the AWS usage cost check and notifications."""
    current_cost = get_current_costs()
    projected_cost, projected_spending = get_end_of_month_projection(
        float(current_cost)
    )
    logger.info(
        {
            "event": "cost_check",
            "timestamp": datetime.now().isoformat(),
            "current_cost": current_cost,
            "projected_cost": projected_cost,
            "projected_spending": projected_spending,
        }
    )
    send_cost_notifications(current_cost, projected_cost, projected_spending)
    try:
        requests.get(HEALTHCHECKS_URL, timeout=10)
        logger.info({"event": "healthcheck_ping", "status": "success"})
    except requests.RequestException as re:
        logger.exception(
            {"event": "healthcheck_ping", "status": "fail", "error": str(re)}
        )


if __name__ == "__main__":
    main()
