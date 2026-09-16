from django.urls import path

from aa_altcorp import views

app_name = "aa_altcorp"
urlpatterns = [
    path("", views.index, name="index"),
    path("contacts/", views.contacts, name="contacts"),
    path("contacts/assign/", views.assign_contact, name="assign_contact"),
    path("contacts/mains/", views.main_character_search, name="main_character_search"),
    path("access-lists/", views.access_lists, name="access_lists"),
    path("character/add_token/", views.add_character_token, name="add_character_token"),
    path("attach/", views.attach, name="attach"),
    path("detach/<int:relationship_id>/", views.detach, name="detach"),
    path("associate-character/", views.associate_character, name="associate_character"),
]
