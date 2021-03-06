import hashlib
from functools import wraps

import python_freeipa
from flask import abort, current_app, flash, g, Markup, redirect, session, url_for
from flask_babel import lazy_gettext as _

from noggin import app
from noggin.representation.user import User
from noggin.security.ipa import maybe_ipa_session


def gravatar(email, size):
    return (
        app.config["AVATAR_SERVICE_URL"]
        + "avatar/"
        + hashlib.md5(email.lower().encode('utf8')).hexdigest()  # nosec
        + "?s="
        + str(size)
        + "&d="
        + app.config["AVATAR_DEFAULT_TYPE"]
    )


# A wrapper that will give us 'ipa' if it exists, or bump the user back to /
# with a message telling them to log in.
def with_ipa():
    def decorator(f):
        @wraps(f)
        def fn(*args, **kwargs):
            ipa = maybe_ipa_session(current_app, session)
            if ipa:
                g.ipa = ipa
                g.current_user = User(g.ipa.user_find(whoami=True)['result'][0])
                return f(*args, **kwargs, ipa=ipa)
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('root'))

        return fn

    return decorator


def require_self(f):
    """Require the logged-in user to be the user that is currently being edited"""

    @wraps(f)
    def fn(*args, **kwargs):
        try:
            username = kwargs["username"]
        except KeyError:
            abort(
                500,
                "The require_self decorator only works on routes that have 'username' "
                "as a component.",
            )
        if session.get('noggin_username') != username:
            flash('You do not have permission to edit this account.', 'danger')
            return redirect(url_for('user', username=username))
        return f(*args, **kwargs)

    return fn


def group_or_404(ipa, groupname):
    group = ipa.group_find(o_cn=groupname, fasgroup=True)['result']
    if not group:
        abort(404, _('Group %(groupname)s could not be found.', groupname=groupname))
    else:
        return group[0]


def user_or_404(ipa, username):
    try:
        return ipa.user_show(a_uid=username)['result']
    except python_freeipa.exceptions.NotFound:
        abort(404)


def undo_button(form_action, submit_name, submit_value, hidden_tag):
    """return an undo button html snippet as a string, to be used in flash messages"""

    undo_text = _("Undo")

    template = f"""
    <span class='ml-auto' id="flashed-undo-button">
        <form action="{form_action}" method="post">
            {hidden_tag}
            <button type="submit" class="btn btn-outline-success btn-sm"
             name="{submit_name}" value="{submit_value}">
                {undo_text}
            </button>
        </form>
    </span>"""
    return Markup(template)
