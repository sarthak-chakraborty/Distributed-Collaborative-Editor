import json
# from urllib.parse import urlparse
from urlparse import urlparse
from django.db import transaction, IntegrityError
from django.http import HttpResponse, HttpResponseBadRequest, \
	HttpResponseNotAllowed, HttpResponseNotFound, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from gripcontrol import Channel, HttpStreamFormat
from django_grip import publish
from .text_operation import TextOperation
from .models import User, Document, DocumentChange
import requests
import time
import threading

STATE = 'master'
MASTER_URL = 'http://127.0.0.1:8001'		# The master server
REPLICA_URLS = [							# All replica urls
	'http://127.0.0.1:8002',
	'http://127.0.0.1:8003'
	# 'http://127.0.0.1:8003' # Add this if we need 3 servers
]
ALIVE_STATUS = [True,True,True]				# Used by master
CURRENT_PRIMARY = 0							# Index of current primary

HEARTBEAT_TIMEOUT = 1						# Time between consequitive heartbeats
HEARTBEAT_MISS_TIMEOUT = 3*HEARTBEAT_TIMEOUT	# Time after which missing heartbeats is considered failure

HB_TIMES = [								# Time when last heartbeat received from replica. 
	time.time(),
	time.time()
	# time.time()	# Add this if we need 3 servers
]	


def crash_detect():
	while(1):
		for i in range(len(HB_TIMES)):
			if ALIVE_STATUS[i] and time.time() - HB_TIMES[i] > HEARTBEAT_MISS_TIMEOUT:
				print('Detected crash. Server {} failed to send heartbeat 3 times'.format(i))
				ALIVE_STATUS[i] = False
				for u in REPLICA_URLS:
					url = u+'/api/change_status/'
					payload = {
						'index': i,
						'status': 'crash'		## one of ['alive','crash']
					}
					requests.post(url,payload)

				if CURRENT_PRIMARY == i:
					print('failed node is primary')
					done = False
					while not done:
						previous_primary = CURRENT_PRIMARY
						CURRENT_PRIMARY = (i+1)%len(REPLICA_URLS)
						requests.get(REPLICA_URLS[previous_primary]+'/api/become_secondary/')
						r = requests.get(REPLICA_URLS[CURRENT_PRIMARY]+'/api/become_primary/')
						if r.ok:
							done = True
					print('New primary is {}'.format(CURRENT_PRIMARY))
					# Should we send info to the others about who is the new primary? 

master_crash = threading.Thread(target=crash_detect)
master_crash.start()

				

def _doc_get_or_create(eid):
	try:
		doc = Document.objects.get(eid=eid)
	except Document.DoesNotExist:
		try:
			doc = Document(eid=eid)
			doc.save()
		except IntegrityError:
			doc = Document.objects.get(eid=eid)
	return doc


def index(request, document_id=None):
	if document_id is None:
		url = REPLICA_URLS[CURRENT_PRIMARY]+'/'
	else:
		url = REPLICA_URLS[CURRENT_PRIMARY]+'/{}/'.format(document_id)
	if request.method == 'GET':
		payload = request.GET.dict()
		response = requests.get(url, payload)
	elif request.method == 'POST':
		payload = request.POST.dict()
		response = requests.post(url, payload)
	return response		
		

def users(request):
	url = REPLICA_URLS[CURRENT_PRIMARY]+'/api/users/'
	if request.method == 'GET':
		payload = request.GET.dict()
		response = requests.get(url,payload)
	elif request.method == 'POST':
		payload = request.POST.dict()
		response = requests.post(url,payload)
	return response		


def user(request, user_id):
	url = REPLICA_URLS[CURRENT_PRIMARY]+'/api/users/{}/'.format(user_id)
	if request.method == 'GET':
		payload = request.GET.dict()
		response = requests.get(url,payload)
	elif request.method == 'POST':
		payload = request.POST.dict()
		response = requests.post(url,payload)
	return response		


def document(request, document_id):
	url = REPLICA_URLS[CURRENT_PRIMARY]+'/api/documents/{}/'.format(document_id)
	if request.method == 'GET':
		payload = request.GET.dict()
		response = requests.get(url,payload)
	elif request.method == 'POST':
		payload = request.POST.dict()
		response = requests.post(url,payload)
	return response		


def document_changes(request, document_id):
	url = REPLICA_URLS[CURRENT_PRIMARY]+'/api/documents/{}/changes/'.format(document_id)
	if request.method == 'GET':
		payload = request.GET.dict()
		response = requests.get(url,payload)
	elif request.method == 'POST':
		payload = request.POST.dict()
		response = requests.post(url,payload)
	return response		


def heartbeat_recv(request):
	## use sender details
	if request.method == 'POST':
		# sender = json.loads(request.POST['sender'])
		sender = request.POST['sender']
		HB_TIMES[int(sender)] = time.time()
		print('hb received from {}'.format(sender))
	return JsonResponse({'ok':'ok'})

		
