from django.shortcuts import render

def home(request):
    return render(request, 'core/home.html')

def pricing(request):
    return render(request, 'core/pricing.html')

def docs(request):
    return render(request, 'core/docs.html')
