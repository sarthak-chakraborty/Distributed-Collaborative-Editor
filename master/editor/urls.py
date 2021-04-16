from django.conf.urls import url
from editor import views

urlpatterns = [
	url(r'^$', views.index, name='index-default'),
	url(r'^api/HB/$',views.heartbeat_recv, name='heart-beat'),
	url(r'^api/documents/(?P<document_id>[^./]+)/$', views.document),
	url(r'^api/documents/(?P<document_id>[^./]+)/changes/$', views.document_changes, name='document-changes'),
	url(r'^(?P<document_id>[^./]+)$', views.index)
]
