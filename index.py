#!/usr/bin/python2.7
#
# AWS Lambda function static site generator plugin: npm
#
# This Lambda function is invoked by CodePipeline. It performs these actions:
# - Download file from the CodePipeline artifact S3 bucket and unZIP
# - Generate the static website from the source
# - ZIP and upload the new site contents to the CodePipeline artifact S3 bucket
# - Notify CodePipeline of the results (success or failure)
#
from __future__ import print_function
from boto3.session import Session
import boto3
import botocore
import os
import zipfile
import tempfile
import shutil
import shlex
import subprocess
import traceback
import os.path

os.environ["PATH"] = os.environ["LAMBDA_TASK_ROOT"] + "/node/bin" + os.pathsep + os.environ["PATH"]

code_pipeline = boto3.client('codepipeline')

def setup(event):
    # Extract attributes passed in by CodePipeline
    job_id = event['CodePipeline.job']['id']
    job_data = event['CodePipeline.job']['data']
    input_artifact = job_data['inputArtifacts'][0]
    config = job_data['actionConfiguration']['configuration']
    credentials = job_data['artifactCredentials']
    from_bucket = input_artifact['location']['s3Location']['bucketName']
    from_key = input_artifact['location']['s3Location']['objectKey']
    from_revision = input_artifact['revision']
    output_artifact = job_data['outputArtifacts'][0]
    to_bucket = output_artifact['location']['s3Location']['bucketName']
    to_key = output_artifact['location']['s3Location']['objectKey']
    user_parameters = config['UserParameters']

    # Temporary credentials to access CodePipeline artifacts in S3
    key_id = credentials['accessKeyId']
    key_secret = credentials['secretAccessKey']
    session_token = credentials['sessionToken']
    session = Session(aws_access_key_id=key_id,
                      aws_secret_access_key=key_secret,
                      aws_session_token=session_token)
    s3 = session.client('s3',
                        config=botocore.client.Config(signature_version='s3v4'))

    return (job_id, s3, from_bucket, from_key, from_revision,
            to_bucket, to_key, user_parameters)

def download_source(s3, from_bucket, from_key, from_revision, source_dir):
    with tempfile.NamedTemporaryFile() as tmp_file:
        #TBD: from_revision is not used here!
        s3.download_file(from_bucket, from_key, tmp_file.name)
        with zipfile.ZipFile(tmp_file.name, 'r') as zip:
            zip.extractall(source_dir)

def upload_site(site_dir, s3, to_bucket, to_key):
    with tempfile.NamedTemporaryFile() as tmp_file:
        site_zip_file = shutil.make_archive(tmp_file.name, 'zip', site_dir)
        s3.upload_file(site_zip_file, to_bucket, to_key)

def handler(event, context):
    try:
        (job_id, s3, from_bucket, from_key, from_revision,
         to_bucket, to_key, user_parameters) = setup(event)

        # Directories for source content, and transformed static site
        source_dir = tempfile.mkdtemp()
        site_dir = tempfile.mkdtemp()

        # Download and unzip the source for the static site
        download_source(s3, from_bucket, from_key, from_revision, source_dir)

        # Generate static website from source
        generate_static_site(source_dir, site_dir, user_parameters)

        # ZIP and and upload transformed contents for the static site
        upload_site(site_dir, s3, to_bucket, to_key)

        # Tell CodePipeline we succeeded
        code_pipeline.put_job_success_result(jobId=job_id)

    except Exception as e:
        print("ERROR: " + repr(e))
        traceback.print_exc()
        # Tell CodePipeline we failed
        code_pipeline.put_job_failure_result(jobId=job_id, failureDetails={'message': e, 'type': 'JobFailed'})

    finally:
      shutil.rmtree(source_dir)
      shutil.rmtree(site_dir)

    return "complete"

def run_command(command):
    print(command)
    try:
        print(subprocess.check_output(command, stderr=subprocess.STDOUT))
    except subprocess.CalledProcessError as e:
        print("ERROR return code: ", e.returncode)
        print("ERROR output: ", e.output)
        raise

def generate_static_site(source_dir, site_dir, user_parameters):
    # Run npm as a static site generator
    # yarn can't run with read-only HOME directory
    prev_home_dir = os.getenv("HOME")
    tmp_home_dir = tempfile.mkdtemp()
    taskdir = os.getcwd()
    try:
        os.environ["HOME"] = tmp_home_dir
        os.chdir(source_dir)
        if os.path.isfile("yarn.lock"):
            run_command(["yarn"])
            run_command(["yarn", "run", "build"])
        else:
            run_command(["npm", "install"])
            run_command(["npm", "run", "build"])
        run_command(["cp", "-a", source_dir + "/build/.", site_dir + "/"])
    finally:
        os.chdir(taskdir)
        if prev_home_dir is None:
            del os.environ["HOME"]
        else:
            os.environ["HOME"] = prev_home_dir
        shutil.rmtree(tmp_home_dir)
