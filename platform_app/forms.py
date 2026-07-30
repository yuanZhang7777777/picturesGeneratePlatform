from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from .models import Batch
from .services import ErpAuthError, authenticate_erp_user


class ERPAuthenticationForm(forms.Form):
    username = forms.CharField(
        label="ERP 用户名",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "autofocus": True,
                "placeholder": "请输入 ERP 用户名",
            }
        ),
    )
    password = forms.CharField(
        label="ERP 密码",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "placeholder": "请输入 ERP 密码",
            }
        ),
    )

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.user_cache = None
        self.erp_token = ""

    def clean(self):
        cleaned = super().clean()
        username = cleaned.get("username")
        password = cleaned.get("password")
        if username and password:
            try:
                self.user_cache, self.erp_token = authenticate_erp_user(username, password)
            except ErpAuthError as exc:
                raise forms.ValidationError("ERP 用户名或密码不正确") from exc
        return cleaned

    def get_user(self):
        return self.user_cache


class FirstPasswordChangeForm(PasswordChangeForm):
    pass


class BatchForm(forms.ModelForm):
    class Meta:
        model = Batch
        fields = ["name", "platform", "site", "global_prompt"]
        widgets = {
            "global_prompt": forms.Textarea(attrs={"rows": 4}),
        }
