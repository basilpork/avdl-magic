import base64
import datetime
import json
import logging
import os
from subprocess import call
import time

from flask import Flask, render_template, request, send_from_directory, url_for
import lxml.html
import redis
import requests
from rq import get_current_job, Queue
from rq.job import Job

import settings
import util

logging.basicConfig(**settings.logging)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = base64.b64encode(os.urandom(40))

redis = redis.from_url(settings.redis_url)
rqueue = Queue(connection=redis)

# Make sure the destination directory exists
try:
    os.mkdir('downloads')
except OSError:
    # Already exists
    pass


def nicetimedelta(ts):
    old_datetime = datetime.datetime.fromtimestamp(float(ts))
    now = datetime.datetime.now()
    difference = now - old_datetime
    if difference.seconds < 3: # don't show '0 seconds ago'
        return "just now"
    elif difference.seconds < 60: # don't show '0 minutes ago'
        return "%d seconds ago" % difference.seconds
    elif difference.seconds < 120: # use singular 'minute'
        return "1 minute ago"
    else:
        return "%d minutes ago" % int(difference.seconds / 60)


def get_files_available(where='downloads', extension='.mp3'):
    """Provides metadata for any files available for download.

    Returns a list of three-element lists:
        [ [ filename, ISO 8601 mtime, size ],
            ...
        ]"""
    files = [f for f in os.listdir(where) if f.endswith(extension)]
    files.sort(key=lambda x: os.path.getmtime(os.path.join(where, x)), reverse=True)
    path = lambda x: os.path.join(where, x)
    files = [
        [
            f,
            nicetimedelta(os.path.getmtime(path(f))),
            os.path.getsize(path(f))
        ] for f in files]
    return files


def validate_url(url):
    """Confirm the input URL is reasonably safe to feed to youtube-dl."""
    if url.startswith('http://'):
        # Standardize on https
        url = url.replace('http://', 'https://')
    if url.startswith('https://www.youtube.com/'):
        # We're good
        return url
    else:
        return None


def queued_job_info():
    jobs = []
    # Show the ten most recent jobs
    for job_id in redis.lrange('alljobs', 0, 9):
        job = rqueue.fetch_job(job_id)
        if job is None:
            continue # don't bother showing the 'deleted' jobs
        job_details = redis.hgetall('job:%s' % job_id)
        job_details['submitted'] = nicetimedelta(job_details['submitted'])
        job_details['status'] = job.get_status()
        jobs.append(job_details)
    #jobs.sort(key=lambda x: x['submitted'], reverse=True)
    return jobs


def sizeof_fmt(num, suffix='B'):
    """Happily used from http://stackoverflow.com/a/1094933"""
    for unit in ['','K','M','G','T','P','E','Z']:
        if abs(num) < 1000.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1000.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


def downloaded_files_info():
    files = get_files_available()
    url = lambda x: url_for('download_file', filename=x)
    files_with_urls = [{
        'name': name,
        'modified': modified,
        'size': sizeof_fmt(size),
        'url': url(name),
    } for name, modified, size in files]
    return files_with_urls


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/enqueue', methods=['POST',])
def enqueue():
    data = json.loads(request.data.decode())
    if 'yturl' not in data:
        response = {
            'error': "The Youtube URL to download must be provided as 'yturl'",
        }
        logger.warn("Rejecting /api/enqueue request missing 'yturl'")
        return json.dumps(response), 400 # bad request

    clean_url = validate_url(data['yturl'])
    if clean_url is None:
        response = {
            'error': "I'm sorry, that doesn't really look like a Youtube URL. :-(",
            'info': "Please try again using a link starting with 'https://www.youtube.com'.",
        }
        logger.warn("Rejecting /api/enqueue request for %s" % data['yturl'])
        return json.dumps(response), 403 # forbidden

    logger.info("Accepting /api/enqueue request for %s" % clean_url)
    job = rqueue.enqueue_call(
        func=util.download,
        args=(clean_url,),
        result_ttl=900 # 15 minutes
    )
    job_id  = job.get_id()
    redis.lpush('alljobs', job_id)
    redis.ltrim('alljobs', 0, 9)
    job_details = {
        'job_id':      job_id,
        'request_url': clean_url,
        'submitted':   time.time(),
        'page_title':  '...', # just a placeholder to keep it pretty
    }
    redis.hmset('job:%s' % job_id, job_details)
    redis.expire('job:%s' % job_id, 86400) # 24 hours
    response = {
        'job_id': job_id,
    }
    return json.dumps(response), 201 # created


@app.route('/api/status')
def status():
    response = {
        'jobs': queued_job_info(),
        'files': downloaded_files_info(),
    }
    return json.dumps(response)


@app.route('/download/<path:filename>')
def download_file(filename):
    """Simple and sufficient. Lets a user download a file we've pulled in."""
    return send_from_directory('downloads', filename, as_attachment=True)


@app.route('/api/jobs/<job_id>')
def job_details(job_id):
    try:
        job = Job.fetch(job_id, connection=redis)
        return json.dumps({'status': job.get_status()})
    except:
        response = {
            'error': "No info, probably deleted.",
        }
        return json.dumps(response), 404 # not found


@app.route('/admin/flushredis')
def flushredis():
    logger.info("Received request to flush redis")
    redis.flushall()
    return "Done"

