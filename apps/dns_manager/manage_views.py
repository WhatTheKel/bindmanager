import functools
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.views import redirect_to_login
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.urls import reverse

from .models import Zone, Record, NameServer, AuditLog
from .forms import ZoneForm, RecordForm, NameServerForm, UserCreateForm, UserEditForm


def staff_required(view_func):
    @functools.wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_staff:
            messages.error(request, 'Staff access is required to use the management panel.')
            return redirect('dns_manager:zone_list')
        return view_func(request, *args, **kwargs)
    return wrapped


def superuser_required(view_func):
    @functools.wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_superuser:
            messages.error(request, 'Superuser access is required.')
            return redirect('dns_manager:manage_dashboard')
        return view_func(request, *args, **kwargs)
    return wrapped


def _audit(request, action, entity_type, entity_id, detail):
    AuditLog.objects.create(
        user=request.user,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail,
    )


# ── Dashboard ────────────────────────────────────────────────────

@staff_required
def dashboard(request):
    zone_stats = Zone.objects.aggregate(
        zone_count=Count('id'),
        dirty_count=Count('id', filter=Q(is_dirty=True)),
    )
    return render(request, 'dashboard.html', {
        'zone_count':   zone_stats['zone_count'],
        'record_count': Record.objects.count(),
        'ns_count':     NameServer.objects.count(),
        'dirty_count':  zone_stats['dirty_count'],
        'recent_logs':  AuditLog.objects.select_related('user').order_by('-created_at')[:8],
        'dirty_zones':  Zone.objects.filter(is_dirty=True).order_by('name')[:5],
    })


# ── Zones ────────────────────────────────────────────────────────

@staff_required
def zone_list(request):
    qs = Zone.objects.annotate(
        record_count=Count('records', distinct=True),
        ns_count=Count('nameservers', distinct=True),
    ).order_by('zone_type', 'name')   # forward < reverse alphabetically
    q  = request.GET.get('q', '').strip()
    zt = request.GET.get('zt', '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    if zt in ('forward', 'reverse'):
        qs = qs.filter(zone_type=zt)
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'manage/zone_list.html', {'page_obj': page_obj, 'q': q, 'zt': zt})


@staff_required
def zone_add(request):
    form = ZoneForm(request.POST or None)
    if form.is_valid():
        zone = form.save(commit=False)
        zone.created_by = request.user
        zone.save()
        form.save_m2m()
        _audit(request, AuditLog.Action.CREATE, 'zone', zone.pk, f'Created zone {zone.name}')
        messages.success(request, f'Zone "{zone.name}" created.')
        return redirect('dns_manager:manage_zone_detail', pk=zone.pk)
    return render(request, 'manage/zone_form.html', {'form': form, 'title': 'Add Zone'})


@staff_required
def zone_detail(request, pk):
    zone = get_object_or_404(Zone.objects.prefetch_related('nameservers'), pk=pk)
    rs = request.GET.get('rs', '').strip()   # record search
    rt = request.GET.get('rt', '').strip()   # record type filter
    records_qs = zone.records.order_by('record_type', 'name')
    if rs:
        records_qs = records_qs.filter(Q(name__icontains=rs) | Q(value__icontains=rs))
    if rt:
        records_qs = records_qs.filter(record_type=rt)
    record_types = zone.records.values_list('record_type', flat=True).distinct().order_by('record_type')
    paginator = Paginator(records_qs, 50)
    records_page = paginator.get_page(request.GET.get('rpage'))
    return render(request, 'manage/zone_detail.html', {
        'zone': zone,
        'records_page': records_page,
        'record_count': paginator.count,
        'rs': rs,
        'rt': rt,
        'record_types': record_types,
    })


@staff_required
def zone_edit(request, pk):
    zone = get_object_or_404(Zone, pk=pk)
    form = ZoneForm(request.POST or None, instance=zone)
    if form.is_valid():
        form.save()
        _audit(request, AuditLog.Action.UPDATE, 'zone', zone.pk, f'Updated zone {zone.name}')
        messages.success(request, f'Zone "{zone.name}" updated.')
        return redirect('dns_manager:manage_zone_detail', pk=zone.pk)
    return render(request, 'manage/zone_form.html', {
        'form': form, 'zone': zone, 'title': f'Edit — {zone.name}',
    })


@staff_required
def zone_delete(request, pk):
    zone = get_object_or_404(Zone, pk=pk)
    if request.method == 'POST':
        name = zone.name
        zone.delete()
        _audit(request, AuditLog.Action.DELETE, 'zone', pk, f'Deleted zone {name}')
        messages.success(request, f'Zone "{name}" deleted.')
        return redirect('dns_manager:manage_zone_list')
    return render(request, 'manage/confirm_delete.html', {
        'object_type': 'Zone',
        'object_name': zone.name,
        'back_url':    reverse('dns_manager:manage_zone_detail', args=[zone.pk]),
    })


# ── Records ──────────────────────────────────────────────────────

@staff_required
def record_add(request, zone_pk):
    zone = get_object_or_404(Zone, pk=zone_pk)
    # Zone set up front so Record.clean() can check for conflicting records.
    form = RecordForm(request.POST or None, instance=Record(zone=zone))
    if form.is_valid():
        record = form.save(commit=False)
        record.zone = zone
        record.created_by = request.user
        record.save()
        _audit(request, AuditLog.Action.CREATE, 'record', record.pk,
               f'Added {record.record_type} "{record.name}" to {zone.name}')
        messages.success(request, f'{record.record_type} record added.')
        return redirect('dns_manager:manage_zone_detail', pk=zone.pk)
    return render(request, 'manage/record_form.html', {
        'form': form, 'zone': zone, 'title': 'Add Record',
    })


@staff_required
def record_edit(request, zone_pk, pk):
    zone = get_object_or_404(Zone, pk=zone_pk)
    record = get_object_or_404(Record, pk=pk, zone=zone)
    form = RecordForm(request.POST or None, instance=record)
    if form.is_valid():
        form.save()
        _audit(request, AuditLog.Action.UPDATE, 'record', record.pk,
               f'Updated {record.record_type} "{record.name}" in {zone.name}')
        messages.success(request, 'Record updated.')
        return redirect('dns_manager:manage_zone_detail', pk=zone.pk)
    return render(request, 'manage/record_form.html', {
        'form': form, 'zone': zone, 'record': record, 'title': 'Edit Record',
    })


@staff_required
def record_delete(request, zone_pk, pk):
    zone = get_object_or_404(Zone, pk=zone_pk)
    record = get_object_or_404(Record, pk=pk, zone=zone)
    if request.method == 'POST':
        label = f'{record.record_type} "{record.name}"'
        record.delete()
        _audit(request, AuditLog.Action.DELETE, 'record', pk,
               f'Deleted {label} from {zone.name}')
        messages.success(request, 'Record deleted.')
        return redirect('dns_manager:manage_zone_detail', pk=zone.pk)
    return render(request, 'manage/confirm_delete.html', {
        'object_type': 'Record',
        'object_name': f'{record.record_type} — {record.name} — {record.value[:60]}',
        'back_url':    reverse('dns_manager:manage_zone_detail', args=[zone.pk]),
    })


# ── NameServers ──────────────────────────────────────────────────

@staff_required
def nameserver_list(request):
    nameservers = NameServer.objects.order_by('name')
    return render(request, 'manage/nameserver_list.html', {'nameservers': nameservers})


@staff_required
def nameserver_add(request):
    form = NameServerForm(request.POST or None)
    if form.is_valid():
        ns = form.save()
        _audit(request, AuditLog.Action.CREATE, 'nameserver', ns.pk,
               f'Created nameserver {ns.name}')
        messages.success(request, f'Nameserver "{ns.name}" added.')
        return redirect('dns_manager:manage_nameserver_list')
    return render(request, 'manage/nameserver_form.html', {
        'form': form, 'title': 'Add Nameserver',
    })


@staff_required
def nameserver_edit(request, pk):
    ns = get_object_or_404(NameServer, pk=pk)
    form = NameServerForm(request.POST or None, instance=ns)
    if form.is_valid():
        form.save()   # a rename re-syncs its zones (NameServer.save)
        _audit(request, AuditLog.Action.UPDATE, 'nameserver', ns.pk,
               f'Updated nameserver {ns.name}')
        messages.success(request, f'Nameserver "{ns.name}" updated.')
        return redirect('dns_manager:manage_nameserver_list')
    return render(request, 'manage/nameserver_form.html', {
        'form': form, 'ns': ns, 'title': f'Edit — {ns.name}',
    })


@staff_required
def nameserver_delete(request, pk):
    ns = get_object_or_404(NameServer, pk=pk)
    if request.method == 'POST':
        name = ns.name
        ns.delete()   # re-syncs its zones (NameServer.delete)
        _audit(request, AuditLog.Action.DELETE, 'nameserver', pk,
               f'Deleted nameserver {name}')
        messages.success(request, f'Nameserver "{name}" deleted.')
        return redirect('dns_manager:manage_nameserver_list')
    return render(request, 'manage/confirm_delete.html', {
        'object_type': 'Nameserver',
        'object_name': ns.name,
        'back_url':    reverse('dns_manager:manage_nameserver_list'),
    })


# ── Audit Log ────────────────────────────────────────────────────

@staff_required
def audit_log(request):
    qs = AuditLog.objects.select_related('user').order_by('-created_at')
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'manage/audit_log.html', {'page_obj': page_obj})


# ── Users ────────────────────────────────────────────────────────

@superuser_required
def user_list(request):
    qs = User.objects.prefetch_related('social_auth').order_by('username')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(email__icontains=q) |
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
        )
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'manage/user_list.html', {'page_obj': page_obj, 'q': q})


@superuser_required
def user_add(request):
    form = UserCreateForm(request.POST or None)
    if form.is_valid():
        user = form.save()
        _audit(request, AuditLog.Action.CREATE, 'user', user.pk, f'Created user {user.username}')
        messages.success(request, f'User "{user.username}" created.')
        return redirect('dns_manager:manage_user_list')
    return render(request, 'manage/user_form.html', {'form': form, 'title': 'Add User', 'is_create': True})


@superuser_required
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    social_auths = list(user.social_auth.all())
    is_sso = bool(social_auths)
    form = UserEditForm(request.POST or None, instance=user, sso=is_sso)
    if form.is_valid():
        form.save()
        _audit(request, AuditLog.Action.UPDATE, 'user', user.pk, f'Updated user {user.username}')
        messages.success(request, f'User "{user.username}" updated.')
        return redirect('dns_manager:manage_user_list')
    return render(request, 'manage/user_form.html', {
        'form': form,
        'user_obj': user,
        'social_auths': social_auths,
        'title': f'Edit — {user.username}',
    })


@superuser_required
def user_delete(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, 'You cannot delete your own account.')
        return redirect('dns_manager:manage_user_list')
    if user.is_superuser:
        messages.error(request, 'Superuser accounts cannot be deleted here.')
        return redirect('dns_manager:manage_user_list')
    if request.method == 'POST':
        username = user.username
        user.delete()
        _audit(request, AuditLog.Action.DELETE, 'user', pk, f'Deleted user {username}')
        messages.success(request, f'User "{username}" deleted.')
        return redirect('dns_manager:manage_user_list')
    return render(request, 'manage/confirm_delete.html', {
        'object_type': 'User',
        'object_name': user.username,
        'back_url':    reverse('dns_manager:manage_user_list'),
    })
