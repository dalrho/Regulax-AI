from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib import messages
from .forms import RegisterForm


def register_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your account request has been submitted successfully. Please sign in.')
            return redirect('login')
    else:
        form = RegisterForm()

    return render(request, 'register/register.html', {'form': form})
