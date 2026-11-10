from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.conf import settings
from .forms import LoginForm


def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    next_url = request.GET.get('next') or request.POST.get('next') or settings.LOGIN_REDIRECT_URL

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            user = form.user
            login(request, user)

            response = redirect(next_url)
            if form.cleaned_data.get('remember_username'):
                # 30 days expiry
                response.set_cookie(
                    'remembered_username',
                    form.cleaned_data.get('username'),
                    max_age=30 * 24 * 60 * 60,
                    httponly=False,
                    samesite='Lax',
                )
            else:
                response.delete_cookie('remembered_username')
            return response
    else:
        remembered_username = request.COOKIES.get('remembered_username', '')
        initial = {}
        if remembered_username:
            initial['username'] = remembered_username
            initial['remember_username'] = True
        form = LoginForm(initial=initial)

    return render(
        request,
        'login/login.html',
        {
            'form': form,
            'next': next_url,
        },
    )


def logout_view(request):
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)

