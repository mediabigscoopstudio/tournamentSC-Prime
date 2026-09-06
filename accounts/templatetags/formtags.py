from django import template
from django.utils.text import slugify

register = template.Library()


@register.filter
def add_class(field, css):
    """Render a bound form field with an extra CSS class on its widget."""
    existing = field.field.widget.attrs.get('class', '')
    classes = (existing + ' ' + css).strip()
    return field.as_widget(attrs={'class': classes})


@register.filter
def field_type(field):
    return field.field.widget.__class__.__name__


@register.filter
def lookup(mapping, key):
    """dict[key] from a template — used to keep a filter dropdown's current
    selection when the admin list page re-renders."""
    try:
        return mapping.get(key, '')
    except AttributeError:
        return ''


# Row-action name -> sprite icon id, for the admin console's icon-only action
# buttons (see template/partials/icons.html). Falls back to a generic bolt for
# any action name not listed here, so a new Action() never renders blank.
_ACTION_ICONS = {
    'publish': 'i-cast',
    'unpublish': 'i-lock',
    'feature': 'i-star',
    'cancel': 'i-x',
    'archive': 'i-archive',
    'restore': 'i-restore',
    'approve': 'i-check',
    'reject': 'i-x',
    'suspend': 'i-ban',
    'activate': 'i-bolt',
    'verify': 'i-shield',
    'revoke': 'i-ban',
    'close': 'i-check',
}


@register.filter
def action_icon(name):
    return _ACTION_ICONS.get(name, 'i-bolt')


# Status display text (get_status_display(), slugified to match the existing
# `badge-{{ value|slugify }}` color classes) -> sprite icon id, for a
# resource that shows status as an icon chip in the actions column instead
# of its own list column (Resource.show_status_in_actions).
_STATUS_ICONS = {
    'draft': 'i-edit',
    'published': 'i-cast',
    'live': 'i-bolt',
    'completed': 'i-check',
    'cancelled': 'i-x',
    'postponed': 'i-clock',
    'scheduled': 'i-cal',
    'pending': 'i-clock',
    'approved': 'i-check',
    'rejected': 'i-x',
}


@register.filter
def status_icon(display_text):
    return _STATUS_ICONS.get(slugify(display_text), 'i-flag')


@register.filter
def zip_with(a, b):
    """Pair up two equal-length sequences for a parallel `{% for %}` — used to
    walk a resource's column definitions alongside a row's rendered cells so
    each <td> can carry its column label for the mobile card layout."""
    return zip(a, b)
