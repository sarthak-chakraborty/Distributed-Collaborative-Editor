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

PREV_PRIMARY=0
LAST_LEASE_SENT=time.time()
LEASE_TIMEOUT=10
LEASE_RESEND_TIMEOUT=10
STATE = 'master'
MASTER_URL = 'http://127.0.0.1:8001'		# The master server
REPLICA_URLS = [							# All replica urls
	'http://127.0.0.1:8002',
	'http://127.0.0.1:8003'
	# 'http://127.0.0.1:8003' # Add this if we need 3 servers
]
ALIVE_STATUS = [False,False,True]				# Used by master
CURRENT_PRIMARY = -1						# Index of current primary

HEARTBEAT_TIMEOUT = 1						# Time between consequitive heartbeats
HEARTBEAT_MISS_TIMEOUT = 3*HEARTBEAT_TIMEOUT	# Time after which missing heartbeats is considered failure

HB_TIMES = [								# Time when last heartbeat received from replica. 
	time.time(),
	time.time()
	# time.time()	# Add this if we need 3 servers
]	

def lease_new():
	pass


def lease_extend_ack(request):
	pass

def lease_extend():
	pass

def is_lease_expired():
	if time.time()-LAST_LEASE_SENT>LEASE_TIMEOUT:
		return True
	else:
		return False

# def is_lease_expired():
	# if time.time()-LAST_LEASE_SENT>LEASE_RESEND_TIMEOUT:
	# 	return True
	# else:
	# 	return False

def crash_detect():
	global CURRENT_PRIMARY
	global LAST_LEASE_SENT
	global ALIVE_STATUS
	time.sleep(5)
	while(1):
		for i in range(len(HB_TIMES)):
			if ALIVE_STATUS[i] and time.time() - HB_TIMES[i] > HEARTBEAT_MISS_TIMEOUT:
				if CURRENT_PRIMARY==i and is_lease_expired():
					ALIVE_STATUS[i] = False
				else:
					ALIVE_STATUS[i] = False
				

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

		
		
def pri_inf():
	global PREV_PRIMARY
	global CURRENT_PRIMARY
	global ALIVE_STATUS
	global LAST_LEASE_SENT
	while(1):
		# if (CURRENT_PRIMARY!=-1):
		# 	PREV_PRIMARY=CURRENT_PRIMARY
		print "At ", time.time()
		print "CURRENT PRIMARY is ", CURRENT_PRIMARY
		print "ALIVE_STATUS[0] is ", ALIVE_STATUS[0]
		print "ALIVE_STATUS[1] is ", ALIVE_STATUS[1]
		print "is lease expired is ", is_lease_expired()
		if (CURRENT_PRIMARY!=-1 and ALIVE_STATUS[CURRENT_PRIMARY]==True and is_lease_expired()):
			# time.sleep(2)
			# if (CURRENT_PRIMARY!=-1 and ALIVE_STATUS[CURRENT_PRIMARY]==True and is_lease_expired()):
			ALIVE_STATUS[CURRENT_PRIMARY]=False
			CURRENT_PRIMARY=-1

		if (CURRENT_PRIMARY==-1 and is_lease_expired()):
			if (ALIVE_STATUS[0]):
				r=requests.get(REPLICA_URLS[0]+'/api/lease_new/')
				LAST_LEASE_SENT=time.time()
				retry=50
				while (not r.ok) and retry>0:
					r=requests.get(REPLICA_URLS[0]+'/api/lease_new/')
					LAST_LEASE_SENT=time.time()
					retry=retry-1
				if r.ok:
					CURRENT_PRIMARY=0
					LAST_LEASE_SENT=time.time()

		if (CURRENT_PRIMARY==-1 and is_lease_expired()):	
			if (ALIVE_STATUS[1]):
				r=requests.get(REPLICA_URLS[1]+'/api/lease_new/')
				LAST_LEASE_SENT=time.time()
				retry=50
				while (not r.ok) and retry>0:
					r=requests.get(REPLICA_URLS[1]+'/api/lease_new/')
					LAST_LEASE_SENT=time.time()
					retry=retry-1
				if r.ok:
					CURRENT_PRIMARY=1
					LAST_LEASE_SENT=time.time()
		time.sleep(2)


pi=threading.Thread(target=pri_inf)
pi.start()

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
	#print(document_id)
	if CURRENT_PRIMARY==-1:
		res=HttpResponse('No primary yet')
		return res
	if document_id is None:
		url = REPLICA_URLS[CURRENT_PRIMARY]+'/'
	else:
		url = REPLICA_URLS[CURRENT_PRIMARY]+'/{}'.format(document_id)

	#print(url)
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
		payload['from-master']=True
		response = requests.get(url,payload)
	elif request.method == 'POST':
		payload = request.POST.dict()
		response = requests.post(url,payload)
	return response		


def document_changes(request, document_id):
	url = REPLICA_URLS[CURRENT_PRIMARY]+'/api/documents/{}/changes/'.format(document_id)
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
		#print('\n\n-----',response,response.status_code,'\n\n')
		if response.status_code == 200:
			resp_content = json.loads(response.text)
			#print('resp',resp_content)
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
	## use sender details
	if request.method == 'POST':
		# sender = json.loads(request.POST['sender'])
		sender = int(request.POST['sender'])
		HB_TIMES[sender] = time.time()
		#print('hb received from {}'.format(sender))
		if(not ALIVE_STATUS[sender]):
			ALIVE_STATUS[sender] = True
			#print('Server {} is up again'.format(sender))
			# requests.get(REPLICA_URLS[sender]+'/api/become_secondary/')
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

		
