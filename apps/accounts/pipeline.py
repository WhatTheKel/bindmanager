def set_staff_flag(backend, user, is_new=False, *args, **kwargs):
    if is_new and not user.is_staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])
