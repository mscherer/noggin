from flask import flash, redirect, render_template, session, url_for
import python_freeipa

from noggin import app
from noggin.form.edit_user import (
    UserSettingsProfileForm,
    UserSettingsKeysForm,
    UserSettingsAddOTPForm,
)
from noggin.representation.group import Group
from noggin.representation.user import User
from noggin.representation.otptoken import OTPToken
from noggin.utility import with_ipa, user_or_404


@app.route('/user/<username>/')
@with_ipa(app, session)
def user(ipa, username):
    user = User(user_or_404(ipa, username))
    # As a speed optimization, we make two separate calls.
    # Just doing a group_find (with all=True) is super slow here, with a lot of
    # groups.
    groups = [Group(g) for g in ipa.group_find(user=username, all=False)['result']]
    managed_groups = [
        Group(g)
        for g in ipa.group_find(membermanager_user=username, all=False)['result']
    ]
    return render_template(
        'user.html', user=user, groups=groups, managed_groups=managed_groups
    )


def _user_mod(ipa, form, username, details):
    try:
        ipa.user_mod(username, **details)
    except python_freeipa.exceptions.BadRequest as e:
        if e.message == 'no modifications to be performed':
            form.errors['non_field_errors'] = [e.message]
        else:
            app.logger.error(
                f'An error happened while editing user {username}: {e.message}'
            )
            form.errors['non_field_errors'] = [e.message]
    else:
        flash('Profile has been succesfully updated.', 'success')
        return redirect(url_for('user', username=username))


@app.route('/user/<username>/settings/profile/', methods=['GET', 'POST'])
@with_ipa(app, session)
def user_settings_profile(ipa, username):
    # TODO: Maybe make this a decorator some day?
    if session.get('noggin_username') != username:
        flash('You do not have permission to edit this account.', 'danger')
        return redirect(url_for('user', username=username))

    user = User(user_or_404(ipa, username))
    form = UserSettingsProfileForm(obj=user)

    if form.validate_on_submit():
        result = _user_mod(
            ipa,
            form,
            username,
            {
                'first_name': form.firstname.data,
                'last_name': form.lastname.data,
                'full_name': '%s %s' % (form.firstname.data, form.lastname.data),
                'display_name': '%s %s' % (form.firstname.data, form.lastname.data),
                'mail': form.mail.data,
                'fasircnick': form.ircnick.data,
                'faslocale': form.locale.data,
                'fastimezone': form.timezone.data,
                'fasgithubusername': form.github.data.lstrip('@'),
                'fasgitlabusername': form.gitlab.data.lstrip('@'),
                'fasrhbzemail': form.rhbz_mail.data,
            },
        )
        if result:
            return result

    return render_template(
        'user-settings-profile.html', user=user, form=form, select="profile"
    )


@app.route('/user/<username>/settings/keys/', methods=['GET', 'POST'])
@with_ipa(app, session)
def user_settings_keys(ipa, username):
    # TODO: Maybe make this a decorator some day?
    if session.get('noggin_username') != username:
        flash('You do not have permission to edit this account.', 'danger')
        return redirect(url_for('user', username=username))

    user = User(user_or_404(ipa, username))
    form = UserSettingsKeysForm(obj=user)

    if form.validate_on_submit():
        result = _user_mod(
            ipa,
            form,
            username,
            {'ipasshpubkey': form.sshpubkeys.data, 'fasgpgkeyid': form.gpgkeys.data},
        )
        if result:
            return result

    # if the form has errors, we don't want to add new fields. otherwise,
    # more fields will show up with every validation error
    if not form.errors:
        # Append 2 empty entries at the bottom of the gpgkeys fieldlist
        for i in range(2):
            form.gpgkeys.append_entry()
            form.sshpubkeys.append_entry()

    return render_template(
        'user-settings-keys.html', user=user, form=form, select="keys"
    )


@app.route('/user/<username>/settings/otp/')
@with_ipa(app, session)
def user_settings_otp(ipa, username):
    # TODO: Maybe make this a decorator some day?
    if session.get('noggin_username') != username:
        flash('You do not have permission to edit this account.', 'danger')
        return redirect(url_for('user', username=username))

    addotpform = UserSettingsAddOTPForm()
    user = User(user_or_404(ipa, username))

    otp_uri = session.get('otp_uri')
    session['otp_uri'] = None

    tokens = [
        OTPToken(t)
        for t in ipa._request(
            'otptoken_find', [], {'ipatokenowner': username, 'all': True}
        )['result']
    ]
    return render_template(
        'user-settings-otp.html',
        addotpform=addotpform,
        user=user,
        select="otp",
        tokens=tokens,
        otp_uri=otp_uri,
    )


@app.route('/user/<username>/settings/otp/add/', methods=['POST'])
@with_ipa(app, session)
def user_settings_otp_add(ipa, username):
    # TODO: Maybe make this a decorator some day?
    if session.get('noggin_username') != username:
        flash('You do not have permission to edit this account.', 'danger')
        return redirect(url_for('user', username=username))

    form = UserSettingsAddOTPForm()
    user = User(user_or_404(ipa, username))

    # we don't show the form in the template if there arent gpgkeys, but check on form
    # submit here anyways.
    if not user.gpgkeys:
        flash(
            'Cannot create an OTP token without a GPG Key. Please add a GPG Key', 'info'
        )
        return redirect(url_for('user_settings_otp', username=username))

    if form.validate_on_submit():
        username = session.get('noggin_username')
        description = form.description.data
        try:
            result = ipa.otptoken_add(
                ipatokenowner=username,
                ipatokenotpalgorithm='sha512',
                description=description,
            )
            session['otp_uri'] = result['uri']
        except python_freeipa.exceptions.FreeIPAError as e:
            flash('Cannot create the token.', 'danger')
            app.logger.error(
                f'An error happened while creating an OTP token for user {username}: {e.message}'
            )

    for field_errors in form.errors.values():
        for error in field_errors:
            flash(error, 'danger')

    return redirect(url_for('user_settings_otp', username=username))
