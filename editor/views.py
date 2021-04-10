import json
from urllib.parse import urlparse
from django.db import transaction, IntegrityError
from django.http import HttpResponse, HttpResponseBadRequest, \
	HttpResponseNotAllowed, HttpResponseNotFound, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from gripcontrol import Channel, HttpStreamFormat
from django_grip import publish
from .text_operation import TextOperation
from .models import User, Document, DocumentChange

"""
isPrimary()
isSecondary()
"""

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
	if not document_id:
		document_id = 'default'

	base_url = '{}://{}{}'.format(
		'https' if request.is_secure() else 'http',
		request.META.get('HTTP_HOST') or 'localhost',
		reverse('index-default'))
	if base_url.endswith('/'):
		base_url = base_url[:-1]

	try:
		doc = Document.objects.get(eid=document_id)
	except Document.DoesNotExist:
		doc = Document(eid=document_id)

	context = {
		'document_id': document_id,
		'document_title': doc.title,
		'document_content': doc.content,
		'document_version': doc.version,
		'base_url': base_url
	}

	resp = render(request, 'editor/index.html', context)
	resp['Cache-Control'] = 'no-store, must-revalidate'
	return resp

def users(request):
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
	if request.method == 'GET':
		user = get_object_or_404(User, id=user_id)
		return JsonResponse(user.export())
	else:
		return HttpResponseNotAllowed(['GET'])

def document(request, document_id):
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
			resp = HttpResponse(body, content_type='text/event-stream')
			parsed = urlparse(reverse('document-changes', args=[document_id]))
			instruct = request.grip.start_instruct()
			instruct.set_next_link('{}?link=true&after={}'.format(
				parsed.path, last_version))
			if len(out) < 50:
				instruct.set_hold_stream()
				instruct.add_channel(Channel(gchannel, prev_id=str(last_version)))
				instruct.set_keep_alive('event: keep-alive\ndata:\n\n; format=cstring', 20)
			return resp
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

			event = 'id: {}\nevent: change\ndata: {}\n\n'.format(
				c.version, json.dumps(c.export()))
			publish(
				gchannel,
				HttpStreamFormat(event),
				id=str(c.version),
				prev_id=str(c.version - 1))

		return JsonResponse({'version': c.version})
	else:
		return HttpResponseNotAllowed(['GET', 'POST'])


"""
1. URL for getting only the change
2. just apply the change and save the changed document
3. send ack
"""


"""
diff URL for heartbeat to know whether the primary is alive or not
if not, elect primary
"""
