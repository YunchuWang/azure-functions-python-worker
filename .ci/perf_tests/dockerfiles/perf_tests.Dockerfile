ARG PYTHON_VERSION=3.8

FROM mcr.microsoft.com/azure-functions/python:4-python$PYTHON_VERSION

# Mounting local machines azure-functions-python-worker and azure-functions-python-library onto it
RUN rm -rf /azure-functions-host/workers/python/${PYTHON_VERSION}/LINUX/X64/azure_functions_worker

# Use the following command to run the docker image with customizible worker and library
VOLUME ["/azure-functions-host/workers/python/${PYTHON_VERSION}/LINUX/X64/azure_functions_worker"]

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true \
    FUNCTIONS_WORKER_PROCESS_COUNT=1 \
    AZURE_FUNCTIONS_ENVIRONMENT=Development \
    FUNCTIONS_WORKER_SHARED_MEMORY_DATA_TRANSFER_ENABLED=1 \
    AzureWebJobsStorage=DefaultEndpointsProtocol=https;AccountName=azpyfuncperfstorage;AccountKey=9gI1UuMc9cG9B2n39tdHjMtcUVzj6PuMZ3L0k/D2AFBlIA2QnrbczH4NXinebnId/Wdey1N9XyrG+AStP+v9RA==;EndpointSuffix=core.windows.net

RUN apt-get --quiet update && \
    apt-get install --quiet -y git && \
    cd /home && \
    git clone https://github.com/YunchuWang/AzFunctionsPythonPerformance.git && \
    mkdir -p /home/site/wwwroot/ && \
    cp -r AzFunctionsPythonPerformance/* /home/site/wwwroot/ && \
    pip install -q -r /home/site/wwwroot/requirements.txt

CMD [ "/azure-functions-host/Microsoft.Azure.WebJobs.Script.WebHost" ]
