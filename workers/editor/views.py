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

STATE = 'primary'		# one of ['primary', 'secondary', 'recovering']
INDEX = 0 				# Index of the current replica - not applicable to master
MASTER_URL = 'http://127.0.0.1:8001'		# The master server
SELF_URL = 'http://127.0.0.1:8002'
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

def heartbeat_sender():
	if STATE in ['primary','secondary']:
		while(1):
			time.sleep(HEARTBEAT_TIMEOUT)
			print('IN background runner')
			payload = {
				'type': 'HB',
				'sender': INDEX,
				'sender_state': STATE
			}
			r2 = requests.post(MASTER_URL+'/api/HB/', data = payload)
			print(r2.reason)
			# print(r2.text)
			if r2.ok:
				print('Heartbeat successfully sent')
			else:
				print('Error in sending hb')

if STATE in ['primary','secondary']:
	sec_hb = threading.Thread(target = heartbeat_sender)
	sec_hb.start()
				

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
	if STATE == 'primary':	
		if not document_id:
			document_id = 'default'

		base_url = '{}://{}{}'.format(
			'https' if request.is_secure() else 'http',
			request.META.get('HTTP_HOST') or 'localhost',
			reverse('index-default'))

		print(base_url)
		if base_url.endswith('/'):
			base_url = base_url[:-1]

		try:
			doc = Document.objects.get(eid=document_id)
		except Document.DoesNotExist:
			doc = Document(eid=document_id)
		
		print("\n DOC:")
		print(doc)
		context = {
			'document_id': document_id,
			'document_title': doc.title,
			'document_content': doc.content,
			'document_version': doc.version,
			'base_url': base_url
		}
		print("\nCONTEXT:")
		print(context)

		# resp = render(request, 'editor/index.html', context)
		# resp['Cache-Control'] = 'no-store, must-revalidate'
		# return resp
		resp = JsonResponse(context)
		resp['Cache-Control'] = 'no-store, must-revalidate'
		return resp


def users(request):
	if STATE == 'primary':
		if request.method == 'POST':
			name = request.POST['name']
			try:
				user = User.objects.get(name=name)
			except User.DoesNotExist:
				try:
					user = User(name=name)
					user.save()
				except IntegrityError:
					user = User.objects.get(name=name)
			return JsonResponse(user.export())
		else:
			return HttpResponseNotAllowed(['POST'])


def user(request, user_id):
	if STATE == 'primary':
		if request.method == 'GET':
			user = get_object_or_404(User, id=user_id)
			return JsonResponse(user.export())
		else:
			return HttpResponseNotAllowed(['GET'])


def document(request, document_id):
	if STATE == 'primary':
		if request.method == 'GET':
			try:
				doc = Document.objects.get(eid=document_id)
			except Document.DoesNotExist:
				doc = Document(eid=document_id)
			resp = JsonResponse(doc.export())
			resp['Cache-Control'] = 'no-store, must-revalidate'
			return resp
		else:
			return HttpResponseNotAllowed(['GET'])


def document_changes(request, document_id):
	if STATE == 'primary':
		gchannel = 'document-{}'.format(document_id)

		if request.method == 'GET':
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

			try:
				doc = Document.objects.get(eid=document_id)
				if after is not None:
					if after > doc.version:
						return HttpResponseNotFound('version in the future')
					changes = DocumentChange.objects.filter(
						document=doc,
						version__gt=after).order_by('version')[:50]
					out = [c.export() for c in changes]
					if len(out) > 0:
						last_version = out[-1]['version']
					else:
						last_version = after
				else:
					out = []
					last_version = doc.version
			except Document.DoesNotExist:
				if after is not None and after > 0:
					return HttpResponseNotFound('version in the future')
				out = []
				last_version = 0

			if sse:
				body = ''
				if not link:
					body += 'event: opened\ndata:\n\n'
				for i in out:
					event = 'id: {}\nevent: change\ndata: {}\n\n'.format(
						i['version'], json.dumps(i))
					body += event


				resp_content = {'body':body,
							   'last_version':last_version,
							   'out':out,
							   'gchannel':gchannel}

				return JsonResponse(resp_content)

				# resp = HttpResponse(body, content_type='text/event-stream')
				# parsed = urlparse(reverse('document-changes', args=[document_id]))
				# instruct = request.grip.start_instruct()
				# instruct.set_next_link('{}?link=true&after={}'.format(
				# 	parsed.path, last_version))
				# if len(out) < 50:
				# 	instruct.set_hold_stream()
				# 	instruct.add_channel(Channel(gchannel, prev_id=str(last_version)))
				# 	instruct.set_keep_alive('event: keep-alive\ndata:\n\n; format=cstring', 20)
				# return resp
			else:
				return JsonResponse({'changes': out})
		elif request.method == 'POST':
			opdata = json.loads(request.POST['op'])
			for i in opdata:
				if not isinstance(i, int) and not isinstance(i, basestring):
					return HttpResponseBadRequest('invalid operation');

			op = TextOperation(opdata)

			request_id = request.POST['request-id']
			parent_version = int(request.POST['parent-version'])
			doc = _doc_get_or_create(document_id)

			saved = False
			with transaction.atomic():
				doc = Document.objects.select_for_update().get(id=doc.id)
				try:
					# already submitted?
					c = DocumentChange.objects.get(
						document=doc,
						request_id=request_id,
						parent_version=parent_version)
				except DocumentChange.DoesNotExist:
					changes_since = DocumentChange.objects.filter(
						document=doc,
						version__gt=parent_version,
						version__lte=doc.version).order_by('version')

					for c in changes_since:
						op2 = TextOperation(json.loads(c.data))
						try:
							op, _ = TextOperation.transform(op, op2)
						except:
							return HttpResponseBadRequest(
								'unable to transform against version {}'.format(c.version))

					try:
						doc.content = op(doc.content)
					except:
						return HttpResponseBadRequest(
							'unable to apply {} to version {}'.format(
							json.dumps(op.ops), doc.version))

					next_version = doc.version + 1
					c = DocumentChange(
						document=doc,
						version=next_version,
						request_id=request_id,
						parent_version=parent_version,
						data=json.dumps(op.ops))
					c.save()
					doc.version = next_version
					doc.save()
					saved = True

			if saved:
				"""
				1. Send c to secondary (diff url) [ATOMIC MULTICAST]
				2. receive acks
				"""
				payload = {
					'request-id':request_id, 
					'parent-version': parent_version, 
					'data':json.dumps(op.ops)}
				response_statuses = []
				for i in range(len(REPLICA_URLS)):
					if REPLICA_URLS[i] == SELF_URL:
						continue
					if ALIVE_STATUS[i]:
						r = requests.post(url+'/api/documents/{}/changes/'.format(document_id), data = payload)
						if r.ok:
							response_statuses.append(True)
						else:
							response_statuses.append(False)
							break

				if all(response_statuses):
					event = 'id: {}\nevent: change\ndata: {}\n\n'.format(
						c.version, json.dumps(c.export()))
					publish(
						gchannel,
						HttpStreamFormat(event),
						id=str(c.version),
						prev_id=str(c.version - 1))
				else:
					with transaction.atomic():
						c.delete()
						old_version = doc.version - 1
						doc.version = old_version
						doc.save()
					return HttpResponseBadRequest(
							'Error in sending data to replica')

			return JsonResponse({'version': c.version})
		else:
			return HttpResponseNotAllowed(['GET', 'POST'])

	elif STATE == 'secondary':
		if request.method == 'POST':

			opdata = json.loads(request.POST['data'])
			op = TextOperation(opdata)
			request_id = request.POST['request-id']
			parent_version = int(request.POST['parent-version'])
			doc = _doc_get_or_create(document_id)
			with transaction.atomic():
				doc = Document.objects.select_for_update().get(id=doc.id)
				try:
					c = DocumentChange.objects.get(
						document=doc,
						request_id=request_id,
						parent_version=parent_version)
					print("Document change already exists!")
				except DocumentChange.DoesNotExist:

					## Is it needed?
					changes_since = DocumentChange.objects.filter(
						document=doc,
						version__gt=parent_version,
						version__lte=doc.version).order_by('version')

					for c in changes_since:
						op2 = TextOperation(json.loads(c.data))
						try:
							op, _ = TextOperation.transform(op, op2)
						except Exception as e:
							print(e)
							return HttpResponseBadRequest(
								'unable to transform against version {}'.format(c.version))

					try:
						doc.content = op(doc.content)
					except Exception as e:
						print(e)
						return HttpResponseBadRequest("Unable to apply change in secondary database")
					
					next_version = doc.version + 1
					c = DocumentChange(
						document=doc,
						version=next_version,
						request_id=request_id,
						parent_version=parent_version,
						data=json.dumps(op.ops))
					c.save()
					doc.version = next_version
					doc.save()
			return JsonResponse({'ok':'ok'})

"""
1. URL for getting only the change
2. just apply the change and save the changed document
3. send ack
"""


"""
diff URL for heartbeat to know whether the primary is alive or not
if not, elect primary
"""


def change_status(request):
	if STATE in ['primary', 'secondary']:
		index = request.POST['index']
		status = request.POST['status']
		if status == 'crash':
			ALIVE_STATUS[index] = False
		elif status == 'alive':
			ALIVE_STATUS[index] = True
	return JsonResponse({'ok':'ok'})

def become_primary(request):
	if STATE == 'secondary':
		STATE = 'primary'
	return JsonResponse({'ok':'ok'})

def become_secondary(request):
	if STATE == 'primary':
		STATE = 'secondary'
	return JsonResponse({'ok':'ok'})
		
