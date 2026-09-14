from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q

from .models import Zone


@login_required
def zone_list(request):
    qs = Zone.objects.annotate(
        record_count=Count('records', distinct=True),
        ns_count=Count('nameservers', distinct=True),
    ).order_by('zone_type', 'name')   # forward before reverse

    dirty_count = Zone.objects.filter(is_dirty=True).count()

    q  = request.GET.get('q', '').strip()
    zt = request.GET.get('zt', '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    if zt in ('forward', 'reverse'):
        qs = qs.filter(zone_type=zt)

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, 'dns_manager/zone_list.html', {
        'page_obj':    page_obj,
        'dirty_count': dirty_count,
        'q':           q,
        'zt':          zt,
    })


@login_required
def zone_detail(request, pk):
    zone = get_object_or_404(Zone.objects.prefetch_related('nameservers'), pk=pk)

    rs = request.GET.get('rs', '').strip()
    rt = request.GET.get('rt', '').strip()

    records_qs = zone.records.order_by('record_type', 'name')
    if rs:
        records_qs = records_qs.filter(Q(name__icontains=rs) | Q(value__icontains=rs))
    if rt:
        records_qs = records_qs.filter(record_type=rt)

    record_types = zone.records.values_list('record_type', flat=True).distinct().order_by('record_type')

    paginator    = Paginator(records_qs, 50)
    records_page = paginator.get_page(request.GET.get('rpage'))

    return render(request, 'dns_manager/zone_detail.html', {
        'zone':         zone,
        'records_page': records_page,
        'record_count': paginator.count,
        'rs':           rs,
        'rt':           rt,
        'record_types': record_types,
    })
