from django.conf.urls import url
from editor import views

urlpatterns = [
	url(r'^$', views.index, name='index-default'),
	url(r'^api/documents/(?P<document_id>[^./]+)/$', views.document),
	url(r'^api/lease_new/$', views.lease_new),
	url(r'^api/documents/(?P<document_id>[^./]+)/changes/$', views.document_changes, name='document-changes'),
	url(r'^api/recovery_module/(?P<document_id>[^./]+)/$', views.recovery_module, name='recovery-module'),
	url(r'^(?P<document_id>[^./]+)$', views.index),
	url(r'^api/become_primary/$', views.become_primary, name='become_primary'),
	url(r'^api/become_secondary/$',views.become_secondary, name='become_secon'),
	url(r'^api/become_recovery/$', views.become_recovery, name='become_recovery'),
	url(r'^api/change_status/$',views.change_status,name='change status'),
	url(r'^api/get_primary/$',views.get_primary,name='get_primary')
]
