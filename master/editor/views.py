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
import random
import threading

STATE = 'master'

MASTER_URL = 'http://127.0.0.1:8001'		# The master server
REPLICA_URLS = [							# All replica urls
	'http://127.0.0.1:8002',
	'http://127.0.0.1:8003'
	# 'http://127.0.0.1:8003' # Add this if we need 3 servers
]
ALIVE_STATUS = [False,False]				# Used by master
CURRENT_PRIMARY = -1							# Index of current primary
RECOVERING_NODES = set()

HEARTBEAT_TIMEOUT = 1						# Time between consequitive heartbeats
HEARTBEAT_MISS_TIMEOUT = 3*HEARTBEAT_TIMEOUT	# Time after which missing heartbeats is considered failure

HB_TIMES = [								# Time when last heartbeat received from replica. 
	time.time(),
	time.time()
	# time.time()	# Add this if we need 3 servers
]

LEASE_SENT=time.time()
LEASE_TIMEOUT=10
def is_lease_expired():
	if (time.time()-LEASE_SENT > LEASE_TIMEOUT):
		return True
	else:
		return False

def monitor():
	global CURRENT_PRIMARY
	global ALIVE_STATUS
	global LEASE_SENT
	while (1):
		print "CURRENT PRIMARY is ", CURRENT_PRIMARY
		print "STATUS of server-0 is ",ALIVE_STATUS[0]
		print "STATUS of server-1 is ",ALIVE_STATUS[1]
		print "is_lease_expired ",is_lease_expired()
		print "Time to expiration of lease ", LEASE_SENT+LEASE_TIMEOUT-time.time()
		if (CURRENT_PRIMARY==-1) and (ALIVE_STATUS[0] and is_lease_expired()):
			r=requests.get(REPLICA_URLS[0]+'/api/lease_new/')
			retry=50
			while not r.ok and retry>0:
				r=requests.get(REPLICA_URLS[0]+'/api/lease_new/')
				retry=retry-1
			if r.ok:
				print "!!!LEASE SENT!!!"
				LEASE_SENT=time.time()
				CURRENT_PRIMARY=0
		if (CURRENT_PRIMARY==-1) and (ALIVE_STATUS[1] and is_lease_expired()):
			r1=requests.get(REPLICA_URLS[1]+'/api/lease_new/')
			retry=50
			while not r1.ok and retry>0:
				r1=requests.get(REPLICA_URLS[1]+'/api/lease_new/')
				retry=retry-1
			if r1.ok:
				LEASE_SENT=time.time()
				CURRENT_PRIMARY=1
		if (is_lease_expired()):
			CURRENT_PRIMARY=-1
		time.sleep(1)
	
mon=threading.Thread(target=monitor)
mon.start()


def crash_detect():
	global CURRENT_PRIMARY
	global RECOVERING_NODES
	time.sleep(5)
	while(1):
		for i in range(len(HB_TIMES)):
			# print('checking server ',i, ALIVE_STATUS[i] and time.time() - HB_TIMES[i] > HEARTBEAT_MISS_TIMEOUT)
			if ALIVE_STATUS[i] and time.time() - HB_TIMES[i] > HEARTBEAT_MISS_TIMEOUT:
				print('Detected crash. Server {} failed to send heartbeat 3 times'.format(i))
				if (i==CURRENT_PRIMARY and not is_lease_expired()):
					ALIVE_STATUS[i] = False
				else:
					ALIVE_STATUS[i] = False
				# ALIVE_STATUS[i] = False
				for u in REPLICA_URLS:
					url = u+'/api/change_status/'
					payload = {
						'index': i,
						'status': 'crash'		## one of ['alive','crash']
					}
					try:
						requests.post(url,payload)
					except:
						pass

				if CURRENT_PRIMARY == i:
					print('failed node is primary')
					done = False
					while not done:
						previous_primary = CURRENT_PRIMARY
						eligible_primary = {i for i in range(len(REPLICA_URLS))} - {CURRENT_PRIMARY} - RECOVERING_NODES
						CURRENT_PRIMARY = random.choice( list(eligible_primary) )
						# CURRENT_PRIMARY = (i+1)%len(REPLICA_URLS)
						try:
							requests.get(REPLICA_URLS[previous_primary]+'/api/become_secondary/')
						except:
							pass
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
		url = REPLICA_URLS[CURRENT_PRIMARY]+'/{}'.format(document_id)

	print(url)
	if request.method == 'GET':
		payload = request.GET.dict()
		payload['from-master'] = True
		response = HttpResponse(requests.get(url, payload).text)

		# context = json.loads(response.text)
		# response = render(request, 'editor/index.html', context)
		response['Cache-Control'] = 'no-store, must-revalidate'
		
	elif request.method == 'POST':
		payload = request.POST.dict()
		response = HttpResponse(requests.post(url, payload).text)
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
	print('url here goes', url)
	if request.method == 'GET':
		gchannel = 'document-{}'.format(document_id)
		link = False
		sse = False
		if request.GET.get('link') == 'true':
			link = True
			sse = True
		else:
			accept = request.META.get('HTTP_ACCEPT')
			if accept and accept.find('text/event-stream') != -1:
				sse = True

		after = None
		last_id = request.grip.last.get(gchannel)
		if last_id:
			after = int(last_id)
		
		if after is None and sse:
			last_id = request.META.get('Last-Event-ID')
			if last_id:
				after = int(last_id)

		if after is None and sse:
			last_id = request.GET.get('lastEventId')
			if last_id:
				after = int(last_id)

		if after is None:
			afterstr = request.GET.get('after')
			if afterstr:
				after = int(afterstr)


		payload = request.GET.dict()
		payload['from-master'] = True
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
		payload['from-master'] = True
		response = requests.post(url, payload)
		print('\n\n-----',response,response.status_code,'\n\n')
		if response.status_code == 200:
			resp_content = json.loads(response.text)
			print('resp',resp_content)
			if "success" in resp_content:
				if resp_content["success"]:
					publish(
						resp_content['gchannel'],
						HttpStreamFormat(json.loads(resp_content['event'])),
						id=str(resp_content['version']),
						prev_id=str(resp_content['version']-1))

				response = JsonResponse({'version': resp_content['version']})

		elif response.status_code == 500:
			response = HttpResponseBadRequest(response.text)
		else:
			response = HttpResponseNotFound('invalid')

	return response		


def heartbeat_recv(request):
	global ALIVE_STATUS
	global HB_TIMES
	global REPLICA_URLS
	global CURRENT_PRIMARY
	global RECOVERING_NODES

	# global ALIVE_STATUS
	# global HB_TIMES
	# global REPLICA_URLS
	# global CURRENT_PRIMARY
	global DOC_ID
	global LEASE_SENT
	## use sender details
	if request.method == 'POST':
		# sender = json.loads(request.POST['sender'])
		sender = int(request.POST['sender'])
		HB_TIMES[sender] = time.time()
		if (sender==CURRENT_PRIMARY):
			r=requests.get(REPLICA_URLS[CURRENT_PRIMARY]+'/api/lease_new')
			retry =50
			while not r.ok and retry>0:
				r=requests.get(REPLICA_URLS[CURRENT_PRIMARY]+'/api/lease_new')
				retry = retry-1
			if r.ok:
				LEASE_SENT=time.time()
		print('hb received from {}'.format(sender))

		# Add the index of sender nodes in a set if they are in 'recovering' state
		if request.POST['sender_state'] == 'recovering':
			RECOVERING_NODES.add(sender)
		elif request.POST['sender_state'] in ['primary', 'secondary']:
			if sender in RECOVERING_NODES:
				RECOVERING_NODES.remove(sender)

		if (not ALIVE_STATUS[sender]):
			ALIVE_STATUS[sender] = True
			print('Server {} is up again'.format(sender))
			payload = {'index' : sender, 'primary_ind' : CURRENT_PRIMARY}
			url = REPLICA_URLS[sender] + '/api/get_primary/'
			try:
				r = requests.post(url, data = payload)
				if(r.ok):
					print('done')
				else:
					print('no')
				print(url,payload)
			except:
				print('POST request failed')
			requests.get(REPLICA_URLS[sender]+'/api/become_recovery/')

			for u in REPLICA_URLS:
				url = u+'/api/change_status/'
				payload = {
					'index': sender,
					'status': 'alive'		## one of ['alive','crash']
				}
				try:
					requests.post(url,payload)
				except:
					pass
			
	return JsonResponse({'ok':'ok'})

		

