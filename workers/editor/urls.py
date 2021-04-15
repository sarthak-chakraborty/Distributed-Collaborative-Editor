from django.conf.urls import url
from editor import views

urlpatterns = [
	url(r'^$', views.index, name='index-default'),
	url(r'^api/users/$', views.users),
	url(r'^api/users/(?P<user_id>[^/]+)/$', views.user),
	url(r'^api/documents/(?P<document_id>[^/]+)/$', views.document),
	url(r'^api/documents/(?P<document_id>[^/]+)/changes/$', views.document_changes, name='document-changes'),
	url(r'^(?P<document_id>[^/]+)$', views.index),
	url(r'^api/become_primary/$', views.become_primary, name='become_primary'),
	url(r'^api/become_secondary/$',views.become_secondary, name='become_secon'),
	url(r'^api/change_status/$',views.change_status,name='change status'),
	url(r'^api/get_primary/$',views.get_primary,name='get_primary')
]
