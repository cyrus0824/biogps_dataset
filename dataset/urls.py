from django.conf.urls import patterns, include, url
import views

urlpatterns = patterns('',
    url(r'^info/$', views.dataset_info, name='dataset info'),
    url(r'^data/$', views.dataset_data, name='dataset data'),  
)

