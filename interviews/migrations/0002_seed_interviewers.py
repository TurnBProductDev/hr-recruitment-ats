from django.db import migrations

# (full name, email). Usernames are the email; accounts start with an unusable
# password (login disabled) so they are assignable interviewers until HR sets one.
INTERVIEWERS = [
    ('Ajin Das', 'ajin.das@turnb.com'),
    ('Ameer C A', 'ameer.ca@turnb.com'),
    ('Mahesh Pillai', 'mahesh.pillai@turnb.com'),
    ('Sreejith K R', 'sreejith.kr@turnb.com'),
    ('Vysakh K', 'vysakh.k@turnb.com'),
    ('Shareef P', 'mohamed.shareef@turnb.com'),
    ('Rufin Ali Khan', 'rufin.alikhan@turnb.com'),
    ('Subha V Menon', 'subha.menon@turnb.com'),
    ('Shenzy Fathima', 'Shenzy.Fathima@turnb.com'),
    ('Amrita Sunilkumar', 'Amrita.Sunilkumar@turnb.com'),
    ('Gopu Shaji', 'gopu.shaji@turnb.com'),
    ('Midhuna', 'Midhuna.Nair@turnb.com'),
    ('Ashly Antony', 'Ashly.Antony@turnb.com'),
]

GROUP = 'Interviewer'


def seed(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Group = apps.get_model('auth', 'Group')
    group, _ = Group.objects.get_or_create(name=GROUP)
    for full_name, email in INTERVIEWERS:
        first, _, last = full_name.partition(' ')
        user, created = User.objects.get_or_create(
            username=email,
            defaults={'email': email, 'first_name': first, 'last_name': last,
                      'is_staff': False, 'is_active': True, 'password': '!'},
        )
        user.groups.add(group)


def unseed(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.filter(username__in=[e for _, e in INTERVIEWERS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('interviews', '0001_initial'),
    ]
    operations = [
        migrations.RunPython(seed, unseed),
    ]
