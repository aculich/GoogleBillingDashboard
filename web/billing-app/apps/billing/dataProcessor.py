__author__ = 'ashwini'

"""Command-line sample application for listing all objects in a bucket using
the Cloud Storage API.

This sample is used on this page:

    https://cloud.google.com/storage/docs/json_api/v1/json-api-python-samples

For more information, see the README.md under /storage.
"""
import json
from apiclient import discovery
from oauth2client.client import GoogleCredentials
from apps.config.apps_config import BUCKET_NAME, ARCHIVE_BUCKET_NAME, log, db_session
import datetime
from apps.billing.models import Usage

from apscheduler.schedulers.background import BackgroundScheduler
import os

run_now = False


def set_scheduler(hour, min):
    global run_now
    scheduler = BackgroundScheduler()
    scheduler.remove_all_jobs()
    log.info(' ----- IN SET SCHEDULER -----')
    if hour is None and min is None:
        scheduler.add_job(data_processor, id='data_processor')
        run_now = True
    else:
        scheduler.add_job(data_processor, 'cron', hour=hour, minute=min, id='data_processor')
    scheduler.start()
    log.info('------ SCHEDULER ADDED -----')
    scheduler.print_jobs()
    return scheduler


def data_processor():
    status = 200
    message = 'Prcoess Complete '
    startTime = datetime.datetime.now()
    try:
        bucket = BUCKET_NAME
        archive_bucket = ARCHIVE_BUCKET_NAME

        # Get the application default credentials. When running locally, these are
        # available after running `gcloud init`. When running on compute
        # engine, these are available from the environment.
        credentials = GoogleCredentials.get_application_default()

        # Construct the service object for interacting with the Cloud Storage API -
        # the 'storage' service, at version 'v1'.
        # You can browse other available api services and versions here:
        # https://developers.google.com/api-client-library/python/apis/
        service = discovery.build('storage', 'v1', credentials=credentials)

        # Make a request to buckets.get to retrieve a list of objects in the
        # specified bucket.
        req = service.buckets().get(bucket=bucket)
        resp = req.execute()

        # print(json.dumps(resp, indent=2))

        # Create a request to objects.list to retrieve a list of objects.
        fields_to_return = \
            'nextPageToken,items(name,size,contentType,metadata(my-key))'
        req = service.objects().list(bucket=bucket, fields=fields_to_return)
        file_count = 0
        log.info('Process Start time --- {0}'.format(startTime))
        # If you have too many items to list in one request, list_next() will
        # automatically handle paging with the pageToken.
        while req:
            resp = req.execute()
            # print(json.dumps(resp, indent=2))
            if len(resp) == 0:
                log.info('############################################################################################')
                log.info('--------- THE BUCKET LIST IS EMPTY --------------')
                log.info('--------- NO FILES TO PROCESS  --------------')
                log.info(resp)
                log.info('############################################################################################')

            else:
                get_filenames(resp, service)

            req = service.objects().list_next(req, resp)


    except Exception as e:
        log.error(' Error in getting Bucket Details - {0}'.format(e[0]))
        message = e[0]
        status = 500

    endTime = datetime.datetime.now()
    log.info('Process End time --- {0}'.format(endTime))

    elapsedTime = endTime - startTime

    time = 'Total Time to Process all the files -- {0}'.format(divmod(elapsedTime.total_seconds(), 60))
    log.info(time)

    global run_now
    log.info(' GLOBAL RUN NOW FLAG --- {0}'.format(run_now))
    if run_now:
        set_scheduler(os.environ.get('SCHEDULER_HOUR'), os.environ.get('SCHEDULER_MIN'))

    response = dict(data=json.dumps(message), status=status, time=time)
    return response


def get_filenames(resp, service):
    try:
        for key, items in resp.iteritems():
            for item in items:
                log.info('############################################################################################')
                filename = item['name']
                
                # ARCHIVE THE FILE FIRST
                copy_resp = copy_file_to_archive(filename, service)
                log.info(copy_resp)
                if len(copy_resp) == 0:
                    log.error(' ERROR IN COPYING FILE --- SKIPPING FILE -- {0} '.format(filename))
                else:
                    log.info(' COPYING FILE DONE ')
                    log.info('Getting file -- {0}'.format(filename))
                    get_file_resp = get_file(filename, service)
                    if len(get_file_resp) == 0:
                        log.error('Error in getting file -- {0}'.format(get_file_resp))
                    else:
                        process_file_resp = process_file(filename, get_file_resp, service)
                        if len(process_file_resp) == 0:
                            log.error('Error in Processing file -- {0}'.format(filename))
                        else:
                            delete_resp = delete_file(filename, service)  # type is string if success

                            if isinstance(delete_resp, dict) and len(delete_resp) == 0:
                                log.error(' Error in Deleting file --- {0} '.format(filename))

                log.info('############################################################################################')



    except Exception as e:
        log.error('Error in accessing File -- {0}'.format(e[0]))


def get_file(filename, service):
    file_content = dict()
    try:

        # Get Payload Data
        req = service.objects().get_media(
            bucket=BUCKET_NAME,
            object=filename)
        file_content = req.execute()

    except Exception as e:
        log.error('Error in getting the file -- {0}, {1}'.format(filename, e[0]))

    return file_content


def process_file(filename, file_content, service):
    insert_resp = dict()
    try:
        log.info('Processing file -- {0} -- STARTING'.format(filename))

        data_list = json.loads(file_content)
        '''
          parse the json and load the data to db
          once loading done move the file to archive folder

          todo : attach timestamp and tmp to file name while processing

        '''
        insert_resp = insert_usage_data(data_list, filename, service)

        log.info('Processing file -- {0} -- ENDING'.format(filename))

    except Exception as e:
        log.error('Error in processing the file -- {0}'.format(e[0]))
    return insert_resp


'''
    Insert billing info in usage table

'''


def insert_usage_data(data_list, filename, service):
    usage = dict()
    try:
        for data in data_list:
            date = data['startTime']
            usage_date = datetime.datetime.strptime(
                date.split("-")[0] + '-' + date.split("-")[1] + '-' + date.split("-")[2], "%Y-%m-%dT%H:%M:%S")
            cost = float(data['cost']['amount'])
            if 'projectNumber' in data:
                project_id = 'ID-' + str(data['projectNumber'])
            else:
                project_id = 'None'
            resource_type = str(data['lineItemId'])
            account_id = str(data['accountId'])
            usage_value = float(data['measurements'][0]['sum'])
            measurement_unit = str(data['measurements'][0]['unit'])
            # log.info('INSERTING DATA INTO DB -- {0}'.format(data))
            usage = Usage(usage_date, cost, project_id, resource_type, account_id, usage_value, measurement_unit)
            db_session.add(usage)

        db_session.commit()
        usage = dict(message=' data has been added to db')
        log.info('DONE adding contents for file -- {0} into the db '.format(filename))

    except Exception as e:
        log.error('Error in inserting data into the DB -- {0}'.format(e[0]))
        db_session.rollback()

    return usage


def copy_file_to_archive(filename, service):
    resp = dict()
    try:

        log.info('Starting to move the file to Archive ---- {0}'.format(filename))

        copy_object = service.objects().copy(sourceBucket=BUCKET_NAME, sourceObject=filename,
                                             destinationBucket=ARCHIVE_BUCKET_NAME, destinationObject=filename,
                                             body={})
        resp = copy_object.execute()

        log.info('DONE Moving of file - {0} to Archive -{1} '.format(filename, ARCHIVE_BUCKET_NAME))

        # delete_moved_file(filename, service)

    except Exception as e:

        log.error('Error in Copying the object to archive folder - {0}'.format(e[0]))

    return resp


def delete_file(filename, service):
    resp = dict()
    try:
        log.info('Starting to Delete the file {0} from {1}'.format(filename, BUCKET_NAME))

        delete_object = service.objects().delete(bucket=BUCKET_NAME, object=filename)
        resp = delete_object.execute()

        log.info('DONE Deleting file - {0}  from - {1} '.format(filename, BUCKET_NAME))

    except Exception as e:
        log.error('Error in deleting the old file - {0}'.format(e[0]))
        # add code to add metadata or rename the file
    return resp


set_scheduler(os.environ.get('SCHEDULER_HOUR'), os.environ.get('SCHEDULER_MIN'))
