FROM python:3.11-buster

WORKDIR /scripts

COPY requirements.txt .

RUN pip install --upgrade pip

RUN pip install --no-cache-dir -r requirements.txt

COPY aws_usage_cost.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "aws_usage_cost.py"]
