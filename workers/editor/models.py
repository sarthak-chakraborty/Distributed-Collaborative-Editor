from __future__ import unicode_literals

import json
from django.db import models

class User(models.Model):
	name = models.CharField(max_length=255, unique=True)

	def export(self):
		out = {}
		out['id'] = unicode(self.id)
		out['name'] = self.name
		return out

class Document(models.Model):
	eid = models.CharField(max_length=64, unique=True)
	title = models.CharField(max_length=255)
	content = models.TextField()
	version = models.IntegerField(default=0)

	def export(self):
		out = {}
		out['id'] = self.eid
		out['title'] = self.title
		out['content'] = self.content
		out['version'] = self.version
		return out

class DocumentChange(models.Model):
	document = models.ForeignKey(Document, on_delete=models.CASCADE)
	version = models.IntegerField(default=0, db_index=True)
	request_id = models.CharField(max_length=64, unique=True)
	time = models.DateTimeField(auto_now_add=True, db_index=True)
	parent_version = models.IntegerField(default=0)
	data = models.TextField()

	class Meta:
		unique_together = (
			('document', 'version'),
			('document', 'request_id', 'parent_version'),
		)

	def export(self):
		out = {}
		out['version'] = self.version
		out['time'] = self.time.isoformat()
		out['op'] = json.loads(self.data)
		return out
