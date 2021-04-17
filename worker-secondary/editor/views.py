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
import Queue
import traceback

STATE = 'secondary'		# one of ['primary', 'secondary', 'recovering']
INDEX = 1 				# Index of the current replica - not applicable to master

MASTER_URL = 'http://127.0.0.1:8001'		# The master server
SELF_URL = 'http://127.0.0.1:8003'
REPLICA_URLS = [							# All replica urls
	'http://127.0.0.1:8002',
	'http://127.0.0.1:8003'
	# 'http://127.0.0.1:8003' # Add this if we need 3 servers
]
ALIVE_STATUS = [True,True]				# Used by master
CURRENT_PRIMARY = 0							# Index of current primary

HEARTBEAT_TIMEOUT = 1						# Time between consequitive heartbeats
HEARTBEAT_MISS_TIMEOUT = 3*HEARTBEAT_TIMEOUT	# Time after which missing heartbeats is considered failure

HB_TIMES = [								# Time when last heartbeat received from replica. 
	time.time(),
	time.time()
	# time.time()	# Add this if we need 3 servers
]	

recovery_q = Queue.Queue(maxsize=0) 

LEASE_RECEIVED=time.time()
def lease_new(request):
	global STATE
	global LEASE_RECEIVED
	print "!!!LEASE RECEIVED!!!"
	LEASE_RECEIVED=time.time()
	if (STATE=='secondary'):
		STATE='primary'
	return HttpResponse('Thanks for the lease')

LEASE_TIMEOUT=10

def sec_maker():
	global STATE
	while (1):
		print "My current state is ", STATE
		if (time.time()-LEASE_RECEIVED>LEASE_TIMEOUT):
			STATE='secondary'
		time.sleep(1)

sm=threading.Thread(target=sec_maker)
sm.start()


def heartbeat_sender():
	if STATE in ['primary','secondary','recovering']:
		while(1):
			time.sleep(HEARTBEAT_TIMEOUT)
			print('IN background runner')
			payload = {
				'type': 'HB',
				'sender': INDEX,
				'sender_state': STATE
			}
			try:
				r2 = requests.post(MASTER_URL+'/api/HB/', data = payload)
				if r2.ok:
					print('Heartbeat successfully sent, Current state ' + STATE)
				else:
					print('Error in sending hb')
			except:
				pass
			# print(r2.reason)
			# print(r2.text)
			# if r2.ok:
			# 	print('Heartbeat successfully sent, Current state ' + STATE)
			# else:
			# 	print('Error in sending hb')

if STATE in ['primary','secondary','recovering']:
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
	if(not request.GET.get('from-master')):
		return HttpResponse('Go to ' + MASTER_URL)
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
			if(not all(ALIVE_STATUS)):
				return HttpResponse('Failed to create new doc')
			response_statuses = []
			for i in range(len(REPLICA_URLS)):
				if REPLICA_URLS[i] == SELF_URL:
					continue
				if ALIVE_STATUS[i]:
					r = requests.post(REPLICA_URLS[i]+'/api/documents/{}/'.format(document_id))
					if r.ok:
						response_statuses.append(True)
					else:
						response_statuses.append(False)
						break
			
			if all(response_statuses):
				doc = _doc_get_or_create(document_id)
			else:
				return HttpResponse('Failed to create new doc')

		context = {
			'document_id': document_id,
			'document_title': doc.title,
			'document_content': doc.content,
			'document_version': doc.version,
			'base_url': base_url
		}
		print("\nCONTEXT:")
		print(context)

		resp = render(request, 'editor/index.html', context)
		# resp['Cache-Control'] = 'no-store, must-revalidate'
		# return resp
		# resp = JsonResponse(context)
		resp['Cache-Control'] = 'no-store, must-revalidate'
		return resp
	else:
		return HttpResponse('Go to ' + MASTER_URL)



def document(request, document_id):
	if STATE == 'secondary':
		doc = _doc_get_or_create(document_id)
		return JsonResponse({'ok':'ok'})


def document_changes(request, document_id):
	if STATE == 'primary':			
		gchannel = 'document-{}'.format(document_id)

		if request.method == 'GET':
			link = False
			sse = False
			if request.GET.get('link') == 'true':
				link = True
				sse = True
			# else:
			# 	accept = request.META.get('HTTP_ACCEPT')
			# 	if accept and accept.find('text/event-stream') != -1:
			# 		sse = True

			# after = None
			# last_id = request.grip.last.get(gchannel)
			# if last_id:
			# 	after = int(last_id)
			
			# if after is None and sse:
			# 	last_id = request.META.get('Last-Event-ID')
			# 	if last_id:
			# 		after = int(last_id)

			# if after is None and sse:
			# 	last_id = request.GET.get('lastEventId')
			# 	if last_id:
			# 		after = int(last_id)

			# if after is None:
			# 	afterstr = request.GET.get('after')
			# 	if afterstr:
			# 		after = int(afterstr)
			after = int(request.GET.get('after'))
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

			# if sse:
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
							'gchannel':gchannel,
							'success':True}

			# print('\n sse = true, sending response - ',resp_content)
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
			# else:
			# 	print('\n sse = true, sending response - ',out)
			# 	return JsonResponse({'changes': out})

		elif request.method == 'POST':
			opdata = json.loads(request.POST['op'])
			for i in opdata:
				if not isinstance(i, int) and not isinstance(i, basestring):
					return HttpResponseBadRequest('invalid operation');

			op = TextOperation(opdata)

			request_id = request.POST['request-id']
			parent_version = int(request.POST['parent-version'])
			print('parent version is', parent_version)
			doc = _doc_get_or_create(document_id)
			print(doc.version, 'is doc version')

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
					
					old_content = doc.content
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
						r = requests.post(REPLICA_URLS[i]+'/api/documents/{}/changes/'.format(document_id), data = payload)
						if r.ok:
							response_statuses.append(True)
						else:
							response_statuses.append(False)
							break

				if all(response_statuses):
					event = 'id: {}\nevent: change\ndata: {}\n\n'.format(
						c.version, json.dumps(c.export()))
					resp_content = {'version':c.version,
								'event':json.dumps(event),
							   'gchannel':gchannel,
							   'success':True}
					return JsonResponse(resp_content)
				else:
					with transaction.atomic():
						c.delete()
						old_version = doc.version - 1
						doc.version = old_version
						doc.content = old_content
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
					# changes_since = DocumentChange.objects.filter(
					# 	document=doc,
					# 	version__gt=parent_version,
					# 	version__lte=doc.version).order_by('version')

					# for c in changes_since:
					# 	op2 = TextOperation(json.loads(c.data))
					# 	try:
					# 		op, _ = TextOperation.transform(op, op2)
					# 	except Exception as e:
					# 		print(e)
					# 		return HttpResponseBadRequest(
					# 			'unable to transform against version {}'.format(c.version))

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

	elif STATE == 'recovering':
		if request.method == 'POST':

			opdata = json.loads(request.POST['data'])
			# op = TextOperation(opdata)
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
					# changes_since = DocumentChange.objects.filter(
					# 	document=doc,
					# 	version__gt=parent_version,
					# 	version__lte=doc.version).order_by('version')

					# for c in changes_since:
					# 	op2 = TextOperation(json.loads(c.data))
					# 	try:
					# 		op, _ = TextOperation.transform(op, op2)
					# 	except Exception as e:
					# 		print(e)
					# 		return HttpResponseBadRequest(
					# 			'unable to transform against version {}'.format(c.version))


					## Add operation transform to recovery queue since it is recovering
					recovery_q.put([document_id, parent_version, opdata, request_id])

					# try:
					# 	doc.content = op(doc.content)
					# except Exception as e:
					# 	print(e)
					# 	return HttpResponseBadRequest("Unable to apply change in secondary database")
					
					# next_version = doc.version + 1
					# c = DocumentChange(
					# 	document=doc,
					# 	version=next_version,
					# 	request_id=request_id,
					# 	parent_version=parent_version,
					# 	data=json.dumps(op.ops))
					# c.save()
					# doc.version = next_version
					# doc.save()
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

def recover():
	global STATE

	print("in recover function, current state is {}".format(STATE))

	if STATE in ['secondary','recovering']:
		all_docs = Document.objects.all()

		for cur_doc in all_docs:
			try:
				print(cur_doc.eid)
				print(cur_doc.id)
				last_version_stored = cur_doc.version
				print("Last version available before crash-{}".format(last_version_stored))
				url = REPLICA_URLS[CURRENT_PRIMARY] + '/api/recovery_module/{}/'.format(cur_doc.eid)
				payload = {'recovery' : True, 'version' : last_version_stored}
				response = requests.post(url, data = payload)
				print(response.text)
				resp_content = json.loads(response.text)
				print("resp_content")
				# opdata = json.loads(resp_content['data'])
				# op = TextOperation(opdata)
				# request_id = resp_content['request-id']
				# parent_version = int(resp_content['parent-version'])
				print('type',type(resp_content))
				changes = json.loads(resp_content['data']) # Is this the right syntax

				# doc = _doc_get_or_create(DOC_ID)

				with transaction.atomic():
					doc = Document.objects.select_for_update().get(eid=cur_doc.eid)
					for chng in changes:
						opdata = chng['op']
						op  = TextOperation(opdata)
						parent_version = chng['parent_version']
						request_id = chng['request_id'] 

						try:
							c = DocumentChange.objects.get(
								document=doc,
								request_id=request_id,
								parent_version=parent_version)
							print("Document change already exists!")
						except DocumentChange.DoesNotExist:


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
			except Exception as e:
				print(e)
				traceback.print_exc()
				# print('No Document of ID={} exists'.format(DOC_ID))

		print("Applied changes, caught up with primary")
		# Now all changes are done and recovery is complete

		#recovery is complete, move beyond while loop

		#Now need to apply changes of the recovery queue
		print("reading from queue")
		while(not recovery_q.empty()):
			cur_change = recovery_q.get()
			document_id = cur_change[0]
			parent_version = cur_change[1]
			op = TextOperation(cur_change[2])
			request_id = cur_change[3]

			with transaction.atomic():
				doc = Document.objects.select_for_update().get(eid=document_id)
				try:
					c = DocumentChange.objects.get(
						document=doc,
						request_id=request_id,
						parent_version=parent_version)
					print("Document change already exists!")
				except DocumentChange.DoesNotExist:

					## Is it needed?
					# changes_since = DocumentChange.objects.filter(
					# 	document=doc,
					# 	version__gt=parent_version,
					# 	version__lte=doc.version).order_by('version')

					# for c in changes_since:
					# 	op2 = TextOperation(json.loads(c.data))
					# 	try:
					# 		op, _ = TextOperation.transform(op, op2)
					# 	except Exception as e:
					# 		print(e)
					# 		return HttpResponseBadRequest(
					# 			'unable to transform against version {}'.format(c.version))


					#Operation trsansform
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
		print("changing state to secondary")
		STATE = 'secondary'








"""
1. Run the above function in a separate thread
2. For Primary, it should not send messages to dead secondaries
3. For sending recovery, Primary should be able to say that this is the last (maybe do it with a version number)
4. Secondary in 'recovering' state must not ask for more changes once it knows that the last change has been received, change STATE='secondary'
5. in document_changes, for 'recovering' state, add the messages in a queue, for secondary state, check if queue has something. 
   If something is present, apply it (block until everything is applied? BETTER i think), else same thing will repeat (that is when the 
   changes in the queue are being applied, add incoming changes to queue).... Or can we do something better? For example, primary wont send 
   anything until recovery is complete... (have to add it in table and send it hoping that incoming changes occur at a slower rate than the 
   recovery mechanism, so that it catches up at some time.)
"""

def recovery_module(request, document_id=None):
	global STATE
	if STATE == 'primary':
		if request.method == 'POST':
			if not request.POST['recovery']:
				return HttpResponseBadRequest('Not needed if not in recovery')

			version = request.POST['version']
			try:
				doc = Document.objects.get(eid=document_id)
			except Document.DoesNotExist:
				print('Document does not exist')
				return HttpResponseBadRequest('No Document of the given ID')

			# TODO: Is it Correct?
			changes = DocumentChange.objects.filter(
						document=doc,
						version__gt= version).order_by('version')[:50]
			# out = [c.export() for c in changes]
			out = []
			for c in changes:
				c_dict = c.export()
				c_dict['parent_version'] = c.parent_version
				c_dict['request_id'] = c.request_id
				out.append(c_dict)
			# changes_since = DocumentChange.objects.filter(
			# 			document=doc,
			# 			version__gt=parent_version,
			# 			version__lte=doc.version).order_by('version')
			# for c in changes_since:
			# 			op2 = TextOperation(json.loads(c.data))
			# 			try:
			# 				op, _ = TextOperation.transform(op, op2)
			# 			except:
			# 				return HttpResponseBadRequest(
			# 					'unable to transform against version {}'.format(c.version))
					
			# \doc_change = DocumentChange.objects.get(document=doc, version=version) 

			payload = {'data': json.dumps(out)}
			return JsonResponse(payload)

		return HttpResponseNotFound('Not a POST request')
	return HttpResponseBadRequest('Not Primary')


def change_status(request):
	global STATE
	global ALIVE_STATUS
	if STATE in ['primary', 'secondary']:
		index = int(request.POST['index'])
		status = request.POST['status']
		if status == 'crash':
			ALIVE_STATUS[index] = False
		elif status == 'alive':
			ALIVE_STATUS[index] = True
	return JsonResponse({'ok':'ok'})


def become_primary(request):
	global STATE
	if STATE == 'secondary':
		STATE = 'primary'
	return JsonResponse({'ok':'ok'})


def become_secondary(request):
	global STATE
	if STATE == 'primary':
		STATE = 'secondary'

	return JsonResponse({'ok':'ok'})


def get_primary(request):
	global STATE
	global CURRENT_PRIMARY
	if request.method == 'POST':
		CURRENT_PRIMARY = int(request.POST['primary_ind'])
		print('Primary is now {}'.format(CURRENT_PRIMARY))
	return JsonResponse({'ok':'ok'})




def become_recovery(request):
	global STATE
	print("becoming recovery state")
	STATE = 'recovering'
		
	recovery_thread = threading.Thread(target=recover)
	recovery_thread.start()

	return JsonResponse({'ok':'ok'})



		

