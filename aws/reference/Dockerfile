FROM python:3.13-slim
WORKDIR /opt/program
COPY aws/reference/requirements.txt /opt/program/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/reference /opt/program/src/reference
COPY src/clean /opt/program/src/clean
COPY src/ingestion /opt/program/src/ingestion
COPY src/features /opt/program/src/features
COPY src/__init__.py /opt/program/src/__init__.py
COPY tools/reference_baseline.py tools/sagemaker_fit_reference.py /opt/program/tools/
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/opt/program/tools/sagemaker_fit_reference.py"]
