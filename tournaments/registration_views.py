from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q
from django.utils import timezone
from .models import Tournament, Team, TeamMembership, TournamentTeamEntry, IndividualRegistration
from accounts.models import Notification
from accounts.models import PlayerProfile
from django.contrib.auth import get_user_model

User = get_user_model()

@login_required
def tournament_join_dispatch(request, slug):
    t = get_object_or_404(Tournament.objects.public(), slug=slug)
    
    if t.status in ('COMPLETED', 'CANCELLED'):
        messages.error(request, 'This tournament is no longer accepting entries.')
        return redirect('tournament_detail', slug=slug)
    if t.registration_deadline and timezone.now() > t.registration_deadline:
        messages.error(request, 'Registration has closed.')
        return redirect('tournament_detail', slug=slug)

    # Derive from the tournament's real format, not the stored field (which
    # defaults to TEAM and is stale for individual sports like chess).
    if not t.is_team_based:
        ptype = 'INDIVIDUAL'
    elif t.draw_category in ('DOUBLES', 'MIXED_DOUBLES'):
        ptype = 'PAIR'
    else:
        ptype = 'TEAM'
    
    # INDIVIDUAL logic
    if ptype == 'INDIVIDUAL':
        if IndividualRegistration.objects.filter(tournament=t, player=request.user.player_profile).exclude(status__in=['REMOVED', 'CANCELLED']).exists():
            messages.info(request, "You are already registered.")
            return redirect('tournament_detail', slug=slug)
            
        if request.method == 'POST':
            phone = request.POST.get('phone_number', '')
            email = request.POST.get('email', '')
            rating = None
            if t.sport.slug == 'chess':
                try:
                    rating = int(request.POST.get('rating', '').strip())
                    if not 0 < rating <= 3500:
                        raise ValueError
                except ValueError:
                    messages.error(request, 'Please enter a valid FIDE rating (1-3500).')
                    return redirect('tournament_detail', slug=slug)
            # Seed = registration order (1 for the first entrant, 2 for the next, ...)
            seed = IndividualRegistration.objects.filter(tournament=t).count() + 1
            # Simple individual registration
            IndividualRegistration.objects.create(
                rating=rating,
                seed=seed,
                tournament=t,
                player=request.user.player_profile,
                display_name=request.user.display_name,
                phone_number=phone,
                email=email,
                status='PENDING', # Skipping payment for MVP unless required
                payment_status='PAYMENT_SUCCESS'
            )
            Notification.push(request.user, f"You've successfully registered for {t.name}.", url=t.get_absolute_url())
            messages.success(request, "Registration successful!")
            return redirect('tournament_detail', slug=slug)
            
        return render(request, 'players/register_individual.html', {'tournament': t})
        
    # PAIR logic
    elif ptype == 'PAIR':
        # Check if already registered
        if TeamMembership.objects.filter(
            player=request.user.player_profile, 
            team__entries__tournament=t
        ).exclude(team__entries__status__in=['REMOVED', 'CANCELLED']).exists():
            messages.info(request, "You are already registered in a pair for this tournament.")
            return redirect('tournament_detail', slug=slug)
            
        if request.method == 'POST':
            partner_username = request.POST.get('partner_username', '').strip()
            if not partner_username:
                messages.error(request, "You must select a partner.")
                return redirect('tournament_join', slug=slug)
                
            try:
                partner_user = User.objects.get(username__iexact=partner_username)
                partner_profile = partner_user.player_profile
            except User.DoesNotExist:
                messages.error(request, "Partner not found.")
                return redirect('tournament_join', slug=slug)
                
            if partner_user == request.user:
                messages.error(request, "You cannot select yourself as a partner.")
                return redirect('tournament_join', slug=slug)
                
            # Check if partner already registered
            if TeamMembership.objects.filter(
                player=partner_profile, 
                team__entries__tournament=t
            ).exclude(team__entries__status__in=['REMOVED', 'CANCELLED']).exists():
                messages.error(request, "Your partner is already registered for this tournament.")
                return redirect('tournament_join', slug=slug)
                
            # Create a Pair team (is_pair = True)
            team_name = f"{request.user.display_name} & {partner_user.display_name}"
            pair_team = Team.objects.create(
                name=team_name,
                sport=t.sport,
                owner=request.user.player_profile,
                is_pair=True
            )
            # Add both as active members
            TeamMembership.objects.create(team=pair_team, player=request.user.player_profile, role='OWNER', roster_status='ACTIVE', display_name=request.user.display_name)
            TeamMembership.objects.create(team=pair_team, player=partner_profile, role='MEMBER', roster_status='ACTIVE', display_name=partner_user.display_name)
            
            # Register the pair
            TournamentTeamEntry.objects.create(
                tournament=t,
                team=pair_team,
                status='PENDING',
                payment_status='PAYMENT_SUCCESS'
            )
            
            Notification.push(request.user, f"Pair registration for {t.name} successful!", url=t.get_absolute_url())
            Notification.push(partner_user, f"{request.user.display_name} registered you for {t.name}!", url=t.get_absolute_url())
            messages.success(request, "Pair Registration successful!")
            return redirect('tournament_detail', slug=slug)
            
        return render(request, 'players/register_pair.html', {'tournament': t})
        
    # TEAM logic
    elif ptype == 'TEAM':
        profile = request.user.player_profile
        # Find teams captained by the user for THIS sport
        eligible_teams = Team.objects.filter(sport=t.sport, owner=profile, is_pair=False)
        
        if request.method == 'POST':
            team_id = request.POST.get('team_id')
            if not team_id:
                messages.error(request, "Please select a team.")
                return redirect('tournament_join', slug=slug)
                
            team = get_object_or_404(Team, id=team_id, owner=profile, sport=t.sport)
            
            # Check if team already registered
            if TournamentTeamEntry.objects.filter(tournament=t, team=team).exclude(status__in=['REMOVED', 'CANCELLED']).exists():
                messages.error(request, "This team is already registered for this tournament.")
                return redirect('tournament_join', slug=slug)
                
            # Validate Roster limits
            active_count = team.memberships.filter(roster_status='ACTIVE').count()
            if t.min_active_players and active_count < t.min_active_players:
                messages.error(request, f"Your team needs at least {t.min_active_players} active players. You have {active_count}.")
                return redirect('tournament_join', slug=slug)
            if t.max_active_players and active_count > t.max_active_players:
                messages.error(request, f"Your team cannot have more than {t.max_active_players} active players. You have {active_count}.")
                return redirect('tournament_join', slug=slug)
                
            # Register team
            TournamentTeamEntry.objects.create(
                tournament=t,
                team=team,
                status='PENDING',
                payment_status='PAYMENT_SUCCESS'
            )
            
            Notification.push(request.user, f"Team {team.name} successfully registered for {t.name}.", url=t.get_absolute_url())
            messages.success(request, f"Team '{team.name}' registered successfully!")
            return redirect('tournament_detail', slug=slug)
            
        return render(request, 'players/register_team.html', {'tournament': t, 'teams': eligible_teams})

    return redirect('tournament_detail', slug=slug)

@login_required
def api_search_partner(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    
    users = User.objects.filter(
        Q(username__icontains=q) | Q(display_name__icontains=q)
    ).exclude(id=request.user.id)[:10]
    
    results = [{'username': u.username, 'name': u.display_name} for u in users]
    return JsonResponse({'results': results})

@login_required
def apply_to_team(request, slug):
    if request.method == 'POST':
        t = get_object_or_404(Tournament.objects.public(), slug=slug)
        team_id = request.POST.get('team_id')
        phone = request.POST.get('phone_number', '').strip()
        email = request.POST.get('email', '').strip()
        
        team = get_object_or_404(Team, id=team_id)
        profile = request.user.player_profile
        
        # Check if already in the team
        if team.memberships.filter(player=profile).exists():
            messages.error(request, "You are already a member of this team.")
            return redirect('tournament_detail', slug=slug)
            
        # Check if already requested
        from tournaments.models import TeamJoinRequest
        if TeamJoinRequest.objects.filter(team=team, player=profile, status='PENDING').exists():
            messages.info(request, "You have already sent a request to this team.")
            return redirect('tournament_detail', slug=slug)
            
        TeamJoinRequest.objects.create(
            team=team,
            player=profile,
            tournament=t,
            phone_number=phone,
            email=email
        )
        
        # Ownerless (organizer-created) teams: the tournament organizer decides.
        approver = team.owner.user if team.owner else t.organizer.user
        Notification.push(approver, f"{request.user.display_name} has requested to join {team.name}.", url=f"/teams/{team.id}/manage")
        messages.success(request, f"Your request to join {team.name} has been sent!")
        
    return redirect('tournament_detail', slug=slug)

@require_POST
def entry_approve(request, slug, entry_id):
    from tournaments.models import TournamentTeamEntry, Tournament
    from accounts.models import Notification
    from django.core.mail import send_mail
    from django.conf import settings
    
    t = get_object_or_404(Tournament, slug=slug)
    # Check owner
    if not (request.user.is_authenticated and t.organizer.user_id == request.user.id):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    entry = get_object_or_404(TournamentTeamEntry, id=entry_id, tournament=t, status='PENDING')
    entry.status = 'APPROVED'
    entry.save()
    
    # Notify team owner
    if not entry.team.owner:
        messages.success(request, f"{entry.team.name} approved.")
        return redirect('participants_manage', slug=slug)
    owner_user = entry.team.owner.user
    Notification.push(owner_user, f"Your team {entry.team.name} was approved for {t.name}!", url=t.get_absolute_url())
    
    # Send Email
    subject = f"Your team is participating in {t.name}!"
    message = f"Congratulations {owner_user.display_name}!\n\nYour team '{entry.team.name}' has been officially approved to participate in {t.name}.\n\nView the tournament here: {settings.SITE_URL}{t.get_absolute_url()}\n\nGood luck!"
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [owner_user.email])
    except Exception:
        pass
        
    return redirect('participants_manage', slug=slug)

@require_POST
def entry_reject(request, slug, entry_id):
    from tournaments.models import TournamentTeamEntry, Tournament
    from accounts.models import Notification
    
    t = get_object_or_404(Tournament, slug=slug)
    if not (request.user.is_authenticated and t.organizer.user_id == request.user.id):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    entry = get_object_or_404(TournamentTeamEntry, id=entry_id, tournament=t, status='PENDING')
    entry.status = 'REJECTED'
    entry.save()
    
    if not entry.team.owner:
        return redirect('participants_manage', slug=slug)
    owner_user = entry.team.owner.user
    Notification.push(owner_user, f"Your team {entry.team.name}'s application for {t.name} was declined.", url=t.get_absolute_url())
    
    return redirect('participants_manage', slug=slug)

@require_POST
def individual_approve(request, slug, reg_id):
    from tournaments.models import IndividualRegistration, Tournament
    from accounts.models import Notification
    from django.core.mail import send_mail
    from django.conf import settings
    
    t = get_object_or_404(Tournament, slug=slug)
    if not (request.user.is_authenticated and t.organizer.user_id == request.user.id):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    reg = get_object_or_404(IndividualRegistration, id=reg_id, tournament=t, status='PENDING')
    reg.status = 'APPROVED'
    reg.save()
    
    user = reg.player.user
    Notification.push(user, f"Your registration for {t.name} was approved!", url=t.get_absolute_url())
    
    # Send Email
    subject = f"You are participating in {t.name}!"
    message = f"Congratulations {user.display_name}!\n\nYour individual registration has been officially approved to participate in {t.name}.\n\nView the tournament here: {settings.SITE_URL}{t.get_absolute_url()}\n\nGood luck!"
    email_to = reg.email if reg.email else user.email
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email_to])
    except Exception:
        pass
        
    return redirect('participants_manage', slug=slug)

@require_POST
def individual_reject(request, slug, reg_id):
    from tournaments.models import IndividualRegistration, Tournament
    from accounts.models import Notification
    
    t = get_object_or_404(Tournament, slug=slug)
    if not (request.user.is_authenticated and t.organizer.user_id == request.user.id):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    reg = get_object_or_404(IndividualRegistration, id=reg_id, tournament=t, status='PENDING')
    reg.status = 'REJECTED'
    reg.save()
    
    user = reg.player.user
    Notification.push(user, f"Your application for {t.name} was declined.", url=t.get_absolute_url())
    
    return redirect('participants_manage', slug=slug)
