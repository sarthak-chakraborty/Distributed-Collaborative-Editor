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
INDEX = 0 
MASTER_URL = 'http://127.0.0.1:8001'		# The master server
REPLICA_URLS = [							# All replica urls
	'http://127.0.0.1:8002',
	'http://127.0.0.1:8003'
	# 'http://127.0.0.1:8003' # Add this if we need 3 servers
]
ALIVE_STATUS = [True,True,True]				# Used by master
CURRENT_PRIMARY = 0							# Index of current primary
IS_SOME_PRIMARY = 1
HEARTBEAT_TIMEOUT = 1						# Time between consequitive heartbeats
HEARTBEAT_MISS_TIMEOUT = 3*HEARTBEAT_TIMEOUT	# Time after which missing heartbeats is considered failure
safe_to_send_new_lease=1
last_heard_from_secondary=time.time()

HB_TIMES = [								# Time when last heartbeat received from replica. 
	time.time(),
	time.time()
	# time.time()	# Add this if we need 3 servers
]	

LEASE_TIMEOUT=15
Lease_begin_time=time.time()
Lease_sent_time=time.time()

def heartbeat_sender():
	global STATE
	global IS_SOME_PRIMARY
	global Lease_sent_time
	global Lease_begin_time
	global ALIVE_STATUS
	global last_heard_from_secondary
	global safe_to_send_new_lease
	global CURRENT_PRIMARY
	while(1):
		if time.time()-Lease_sent_time >= LEASE_TIMEOUT and STATE=='master':
			print("Primary's lease has expired")
			ALIVE_STATUS[CURRENT_PRIMARY]=False
			# STATE='secondary'
			IS_SOME_PRIMARY=0

		if time.time()-last_heard_from_secondary>= LEASE_TIMEOUT-1 and STATE=='master':
			print("secondary may be dead")
			ALIVE_STATUS[3-CURRENT_PRIMARY]=False

		if IS_SOME_PRIMARY == 0 and STATE=='master':
			if ALIVE_STATUS[3-CURRENT_PRIMARY]==True:
				print("Trying to switch primary to {}".format(3-CURRENT_PRIMARY))
				payload = {
					'type': 'become_primary',
					'sender': INDEX,
					'sender_state': STATE
				}
				r2 = requests.post(REPLICA_URLS[2-CURRENT_PRIMARY]+'/api/become_primary/', data = payload)
				print(r2.reason)
				# print(r2.text)
				if r2.ok:
					print("Become primary request successfully sent by me i.e {}".format(INDEX))
				else:
					print('Error in sending become primary')
		time.sleep(1)

sec_hb = threading.Thread(target = heartbeat_sender)
sec_hb.start()

'''
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
'''
				

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
	print(document_id)
	if document_id is None:
		url = REPLICA_URLS[CURRENT_PRIMARY]+'/'
	else:
		url = REPLICA_URLS[CURRENT_PRIMARY]+'/{}/'.format(document_id)

	print(url)
	if request.method == 'GET':
		payload = request.GET.dict()
		response = requests.get(url, payload)

		context = json.loads(response.text)
		response = render(request, 'editor/index.html', context)
		response['Cache-Control'] = 'no-store, must-revalidate'
		
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
		response = requests.get(url, payload)

		resp_content = json.loads(response.text)
		if "success" in resp_content:
			if resp_content["success"]:
				response = HttpResponse(resp_content['body'], content_type='text/event-stream')
				parsed = urlparse(reverse('document-changes', args=[document_id]))
				instruct = request.grip.start_instruct()
				instruct.set_next_link('{}?link=true&after={}'.format(parsed.path, resp_content['last_version']))
				if len(resp_content['out']) < 50:
					instruct.set_hold_stream()
					instruct.add_channel(Channel(resp_content['gchannel'], prev_id=str(resp_content['last_version'])))
					instruct.set_keep_alive('event: keep-alive\ndata:\n\n; format=cstring', 20)
		else:
			response = HttpResponseNotFound('version in the future')

	elif request.method == 'POST':
		payload = request.POST.dict()
		response = requests.post(url, payload)

		if response.status_code == 200:
			resp_content = json.loads(response.text)
			if "success" in resp_content:
				if resp_content["success"]:
					publish(
						resp_content['gchannel'],
						HttpStreamFormat(resp_content['event']),
						id=str(resp_content['c.version']),
						prev_id=str(resp_content['c.version - 1']))

				response = JsonResponse({'version': resp_content['version']})

		elif response.status_code == 500:
			response = HttpResponseBadRequest(response.text)
		else:
			response = HttpResponseNotFound('invalid')

	return response		


def heartbeat_recv(request):
	## use sender details

	global STATE
	global IS_SOME_PRIMARY
	global Lease_sent_time
	global Lease_begin_time
	global ALIVE_STATUS
	global last_heard_from_secondary
	global safe_to_send_new_lease
	global CURRENT_PRIMARY

	# if request.method == 'POST':
	# 	# sender = json.loads(request.POST['sender'])
	# 	sender = request.POST['sender']
	# 	HB_TIMES[int(sender)] = time.time()
	# 	print('hb received from {}'.format(sender))
	# return JsonResponse({'ok':'ok'})

	if request.method == 'POST':
		sender = int(request.POST['sender'])
		if sender == 'secondary':
			ls1hbr = time.time()
		print "hb received at myself (",STATE,") from ", sender
		time.sleep(0.5)
		print "current primary is ", CURRENT_PRIMARY
		print "STATE=='master' ", STATE=='master'
		print "sender==CURRENT_PRIMARY ", sender==CURRENT_PRIMARY
		print "STATE=='master' and sender==CURRENT_PRIMARY ", STATE=='master' and sender==CURRENT_PRIMARY

		if STATE=='master' and request.POST['type']=='lease_extend_ack':
			print "received lease_extend_ack"
			if int(request.POST['sender'])==CURRENT_PRIMARY:
				print "Setting Lease_sent_time now"
				Lease_sent_time=time.time()
				safe_to_send_new_lease=1
			else:
				print "There is some freaky freaky error"
		
		if STATE=='master' and sender==CURRENT_PRIMARY:
			if safe_to_send_new_lease==1:
				print "sending new lease to primary"
				payload = {
						'type': 'lease_extend',
						'sender': INDEX,
						'sender_state': STATE
				}
				r2 = requests.post(REPLICA_URLS[CURRENT_PRIMARY-1]+'/api/HB/', data = payload)
				print(r2.reason)
				# print(r2.text)
				if r2.ok:
					print "Lease extension HB successfully sent by me i.e ", INDEX
					safe_to_send_new_lease=0
				else:
					print('Error in sending hb')

		if STATE=='master' and sender==3-CURRENT_PRIMARY:
			last_heard_from_secondary=time.time()

	return JsonResponse({'ok':'ok'})

		
