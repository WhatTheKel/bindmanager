from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import NameServer, Zone, Record

_INPUT = {'class': 'form-input'}
_SELECT = {'class': 'form-input form-select'}
_NUMBER = {'class': 'form-input'}


class NameServerForm(forms.ModelForm):
    class Meta:
        model = NameServer
        fields = ['name', 'address', 'config_dir', 'is_active']
        widgets = {
            'name':       forms.TextInput(attrs={**_INPUT, 'placeholder': 'ns1.example.com'}),
            'address':    forms.TextInput(attrs={**_INPUT, 'placeholder': '192.168.1.1'}),
            'config_dir': forms.TextInput(attrs={**_INPUT, 'placeholder': '/etc/bind'}),
        }


class ZoneForm(forms.ModelForm):
    nameservers = forms.ModelMultipleChoiceField(
        queryset=NameServer.objects.filter(is_active=True).order_by('name'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Nameservers that will serve this zone.',
    )

    class Meta:
        model = Zone
        fields = [
            'name', 'zone_type', 'ip_version', 'nameservers',
            'refresh', 'retry', 'expire', 'minimum_ttl', 'default_ttl',
        ]
        widgets = {
            'name':        forms.TextInput(attrs={**_INPUT, 'placeholder': 'example.com'}),
            'zone_type':   forms.Select(attrs=_SELECT),
            'ip_version':  forms.Select(attrs=_SELECT),
            'refresh':     forms.NumberInput(attrs=_NUMBER),
            'retry':       forms.NumberInput(attrs=_NUMBER),
            'expire':      forms.NumberInput(attrs=_NUMBER),
            'minimum_ttl': forms.NumberInput(attrs=_NUMBER),
            'default_ttl': forms.NumberInput(attrs=_NUMBER),
        }


class RecordForm(forms.ModelForm):
    class Meta:
        model = Record
        fields = ['name', 'record_type', 'ttl', 'priority', 'value', 'is_active']
        widgets = {
            'name':        forms.TextInput(attrs={**_INPUT, 'placeholder': 'www  (@ for zone apex)'}),
            'record_type': forms.Select(attrs={**_SELECT, 'id': 'id_record_type'}),
            'ttl':         forms.NumberInput(attrs={**_NUMBER, 'placeholder': 'Leave blank for zone default'}),
            'priority':    forms.NumberInput(attrs={**_NUMBER, 'placeholder': '10'}),
            'value':       forms.Textarea(attrs={'class': 'form-input form-textarea', 'rows': 3}),
        }


class UserCreateForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'is_staff', 'is_superuser', 'is_active']
        widgets = {
            'username':   forms.TextInput(attrs=_INPUT),
            'email':      forms.EmailInput(attrs=_INPUT),
            'first_name': forms.TextInput(attrs=_INPUT),
            'last_name':  forms.TextInput(attrs=_INPUT),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update(_INPUT)
        self.fields['password2'].widget.attrs.update(_INPUT)


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'is_staff', 'is_superuser', 'is_active']
        widgets = {
            'username':   forms.TextInput(attrs=_INPUT),
            'email':      forms.EmailInput(attrs=_INPUT),
            'first_name': forms.TextInput(attrs=_INPUT),
            'last_name':  forms.TextInput(attrs=_INPUT),
        }

    def __init__(self, *args, sso=False, **kwargs):
        super().__init__(*args, **kwargs)
        if sso:
            for field_name in ('username', 'email', 'first_name', 'last_name'):
                self.fields[field_name].disabled = True
