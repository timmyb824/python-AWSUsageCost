# pylint: disable=logging-fstring-interpolation
import calendar
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import requests
from rocketry import Rocketry
from rocketry.conds import every  # daily, hourly,

app = Rocketry()

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
HEALTHCHECKS_URL = os.environ["HEALTHCHECKS_URL_AWS_USAGE_COST"]
THRESHOLD = float(os.environ["THRESHOLD"])
GOTIFY_HOST = os.environ["GOTIFY_HOST"]
GOTIFY_TOKEN = os.environ["GOTIFY_TOKEN_ADHOC_SCRIPTS"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
NTFY_ACCESS_TOKEN = os.environ["NTFY_ACCESS_TOKEN"]
NTFY_URL = f"https://ntfy.timmybtech.com/{NTFY_TOPIC}"
INTERVAL_SCHEDULE = os.environ["INTERVAL_SCHEDULE"]
AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler()],
)

logger = logging.getLogger(__name__)


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


def send_gotify_notification(message) -> dict:
    """Send a notification using Gotify."""
    try:
        response = requests.post(
            f"{GOTIFY_HOST}/message?token={GOTIFY_TOKEN}",
            json={"message": message, "priority": 5},
            timeout=10,
        )
        response.raise_for_status()
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.exception(f"Failed to send Gotify notification. Exception: {exception}")
        return {"ok": False, "status": "Failed"}


def send_discord_notification(message) -> dict:
    """Send a notification using Discord."""
    data = {"content": message}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL, json=data, headers=headers, timeout=5
        )
        response.raise_for_status()
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.exception(f"Failed to send Discord notification. Exception: {exception}")
        return {"ok": False, "status": "Failed"}


def send_ntfy_notification(message) -> dict:
    """Send a notification using Ntfy."""
    try:
        response = requests.post(
            NTFY_URL,
            headers={"Authorization": f"Bearer {NTFY_ACCESS_TOKEN}"},
            data=message,
            timeout=10,
        )
        response.raise_for_status()
        return {"ok": True, "status": response.status_code}
    except Exception as exception:
        logger.exception(f"Failed to send Ntfy notification. Exception: {exception}")
        return {"ok": False, "status": "Failed"}


def check_threshold_exceeded(projected_cost: float) -> bool | None:
    """Check if the projected cost exceeds the threshold and send notifications."""
    if projected_cost <= THRESHOLD:
        return
    message = f"ATTENTION! Projected end-of-month AWS costs of {projected_cost:.2f} USD exceeds {THRESHOLD} USD!"
    discord_response = send_discord_notification(message)
    gotify_response = send_gotify_notification(message)
    ntfy_response = send_ntfy_notification(message)
    if discord_response["ok"] and gotify_response["ok"] and ntfy_response["ok"]:
        logger.info("Discord, Gotify, and Ntfy notifications sent successfully.")
        return True
    else:
        if discord_response["ok"]:
            logger.info("Discord notification sent successfully.")
        if gotify_response["ok"]:
            logger.info("Gotify notification sent successfully.")
        if ntfy_response["ok"]:
            logger.info("Ntfy notification sent successfully.")
        logger.error("Failed to send notifications.")
        return False


# @app.task(daily.at("22:30"))
# @app.task(every("24 hours"))
@app.task(every(f"{INTERVAL_SCHEDULE}"))
def main():
    """Run the main script."""
    # logging.info('Script started successfully.')
    current_cost = get_current_costs()
    projected_cost, projected_spending = get_end_of_month_projection(
        float(current_cost)
    )
    logger.info(f"Current date and time: {datetime.now()}")
    logger.info(f"Current month costs: {current_cost} USD")
    logger.info(f"Projected end-of-month costs: {projected_cost:.2f} USD")
    logger.info(f"Projected end-of-month spending: {projected_spending:.2f} USD")
    if not check_threshold_exceeded(projected_cost):
        logger.info("No threshold exceeded.")
    try:
        requests.get(HEALTHCHECKS_URL, timeout=10)
    except requests.RequestException as re:
        logger.exception(f"Failed to send health check signal. Exception: {re}")


if __name__ == "__main__":
    app.run()
