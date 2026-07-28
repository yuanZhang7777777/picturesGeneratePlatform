from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from .models import Batch


class FirstPasswordChangeForm(PasswordChangeForm):
    pass


class BatchForm(forms.ModelForm):
    class Meta:
        model = Batch
        fields = ["name", "platform", "site", "global_prompt"]
        widgets = {
            "global_prompt": forms.Textarea(attrs={"rows": 4}),
        }
