import datetime
import os
import tempfile

import pytest
import python_freeipa
from vcr import VCR

from noggin import ipa_admin
from noggin.app import app
from noggin.representation.agreement import Agreement
from noggin.representation.otptoken import OTPToken
from noggin.security.ipa import maybe_ipa_login, untouched_ipa_client


@pytest.fixture(scope="session")
def ipa_cert():
    """Create a CA cert usable for tests.

    The FreeIPA CA cert file must exist for client requests to work. Use the one provided by a local
    install of FreeIPA if available, use an empty file otherwise (but in that case the requests must
    have been recorded by VCR).
    """
    with tempfile.NamedTemporaryFile(
        prefix="ipa-ca-", suffix=".crt", delete=False
    ) as cert:
        if os.path.exists("/etc/ipa/ca.crt"):
            # Copy the proper CA file so tests that have no VCR cassette can run
            with open("/etc/ipa/ca.crt", "rb") as orig_ca:
                cert.write(orig_ca.read())
        else:
            # FreeIPA is not installed, this may be CI, just use an empty file
            # because VCR will mock the requests anyway.
            pass
        cert.close()
        app.config['FREEIPA_CACERT'] = cert.name
        yield


@pytest.fixture
def client(ipa_cert):
    app.config['TESTING'] = True
    app.config['DEBUG'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as client:
        with app.app_context():
            yield client


@pytest.fixture(scope='module')
def vcr_cassette_dir(request):
    # Put all cassettes in cassettes/{module}/{test}.yaml
    test_dir = request.node.fspath.dirname
    module_name = request.module.__name__.split(".")[-1]
    return os.path.join(test_dir, 'cassettes', module_name)


@pytest.fixture(scope="session")
def vcr_session(request):
    """Setup VCR at session-level.

    Borrowed from python-vcr.
    """
    test_dir = os.path.abspath(os.path.dirname(__file__))
    cassette_dir = os.path.join(test_dir, 'cassettes')
    kwargs = dict(
        cassette_library_dir=cassette_dir, path_transformer=VCR.ensure_suffix(".yaml")
    )
    record_mode = request.config.getoption('--vcr-record')
    if record_mode:
        kwargs['record_mode'] = record_mode
    if request.config.getoption('--disable-vcr'):
        # Set mode to record but discard all responses to disable both recording and playback
        kwargs['record_mode'] = 'new_episodes'
        kwargs['before_record_response'] = lambda *args, **kwargs: None
    vcr = VCR(**kwargs)
    yield vcr


@pytest.fixture(scope="session")
def ipa_testing_config(vcr_session):
    """Setup IPA with a testing configuration."""
    with vcr_session.use_cassette("ipa_testing_config"):
        pwpolicy = ipa_admin.pwpolicy_show()
        try:
            ipa_admin.pwpolicy_mod(o_krbminpwdlife=0, o_krbpwdminlength=8)
        except python_freeipa.exceptions.BadRequest as e:
            if not e.message == "no modifications to be performed":
                raise
        yield
        try:
            ipa_admin.pwpolicy_mod(
                o_krbminpwdlife=pwpolicy['result']['krbminpwdlife'][0],
                o_krbpwdminlength=pwpolicy['result']['krbpwdminlength'][0],
            )
        except python_freeipa.exceptions.BadRequest as e:
            if not e.message == "no modifications to be performed":
                raise


@pytest.fixture
def make_user(ipa_testing_config):
    created_users = []

    def _make_user(name):
        now = datetime.datetime.utcnow().replace(microsecond=0)
        password = f'{name}_password'
        ipa_admin.user_add(
            a_uid=name,
            o_givenname=name.title(),
            o_sn='User',
            o_cn=f'{name.title()} User',
            o_mail=f"{name}@example.com",
            o_userpassword=password,
            o_loginshell='/bin/bash',
            fascreationtime=f"{now.isoformat()}Z",
        )
        ipa = untouched_ipa_client(app)
        ipa.change_password(name, password, password)
        created_users.append(name)

    yield _make_user

    for name in created_users:
        ipa_admin.user_del(name)


@pytest.fixture
def dummy_user(make_user):
    make_user("dummy")
    yield


@pytest.fixture
def dummy_user_with_case(make_user):
    make_user("duMmy")
    yield


@pytest.fixture
def dummy_group(ipa_testing_config):
    ipa_admin.group_add(
        a_cn='dummy-group',
        o_description="A dummy group",
        fasgroup=True,
        fasurl="http://dummygroup.org",
        fasmailinglist="dummy@mailinglist.org",
        fasircchannel="irc:///freenode.net/#dummy-group",
    )
    yield
    ipa_admin.group_del(a_cn='dummy-group')


@pytest.fixture
def dummy_user_as_group_manager(logged_in_dummy_user, dummy_group):
    """Make the dummy user a manager of the dummy-group group."""
    ipa_admin.group_add_member(a_cn="dummy-group", o_user="dummy")
    ipa_admin.group_add_member_manager(a_cn="dummy-group", o_user="dummy")
    yield


@pytest.fixture
def password_min_time(dummy_group):
    ipa_admin.pwpolicy_add(
        a_cn="dummy-group", o_krbminpwdlife=1, o_cospriority=10, o_krbpwdminlength=8
    )


@pytest.fixture
def logged_in_dummy_user(client, dummy_user):
    with client.session_transaction() as sess:
        ipa = maybe_ipa_login(
            app, sess, username="dummy", userpassword="dummy_password"
        )
    yield ipa
    ipa.logout()
    with client.session_transaction() as sess:
        sess.clear()


@pytest.fixture
def dummy_user_with_gpg_key(client, dummy_user):
    ipa_admin.user_mod(a_uid="dummy", fasgpgkeyid=["dummygpgkeyid"])


@pytest.fixture
def dummy_user_with_otp(client, logged_in_dummy_user):
    ipa = logged_in_dummy_user
    result = ipa.otptoken_add(
        o_ipatokenowner="dummy",
        o_ipatokenotpalgorithm='sha512',
        o_description="dummy's token",
    )
    token = OTPToken(result['result'])
    yield token
    # Deletion needs to be done as admin to remove the last token
    try:
        ipa_admin.otptoken_del(token.uniqueid)
    except python_freeipa.exceptions.NotFound:
        pass  # Already deleted


@pytest.fixture
def cleanup_dummy_tokens():
    yield
    tokens = ipa_admin.otptoken_find(a_criteria="dummy")
    for token in [OTPToken(t) for t in tokens["result"]]:
        ipa_admin.otptoken_del(a_ipatokenuniqueid=token.uniqueid)


@pytest.fixture
def dummy_agreement():
    agreement = ipa_admin.fasagreement_add(
        "dummy agreement", description="i agree to dummy"
    )
    yield Agreement(agreement)
    ipa_admin.fasagreement_del("dummy agreement")
