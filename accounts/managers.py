from django.contrib.auth.models import BaseUserManager
import random


class UserManager(BaseUserManager):
    """Email is the login identifier. Username is auto-derived if omitted."""

    use_in_migrations = True

    def _make_username(self, email):
        base = (email or '').split('@')[0] or 'user'
        candidate = base
        i = 1
        while self.model.objects.filter(username=candidate).exists():
            i += 1
            candidate = f'{base}{i}'
        return candidate

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError('Users must have an email address')
        email = self.normalize_email(email)
        extra.setdefault('username', self._make_username(email))
        
        if 'uid' not in extra:
            while True:
                uid = random.randint(100000, 999999)
                if not self.model.objects.filter(uid=uid).exists():
                    extra['uid'] = uid
                    break
                    
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault('is_staff', True)
        extra.setdefault('is_superuser', True)
        if extra.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(email, password, **extra)
