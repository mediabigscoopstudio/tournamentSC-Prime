from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from .models import Team, TeamMembership, Sport
from accounts.models import PlayerProfile

from io import BytesIO
from PIL import Image
from django.core.files.uploadedfile import InMemoryUploadedFile
import sys




def _can_manage_team(team, user):
    """Owner manages their team. Ownerless (organizer-created) teams are managed
    by the organizer of any tournament the team is entered in."""
    profile = getattr(user, 'player_profile', None)
    if team.owner_id is not None:
        return profile is not None and team.owner_id == profile.pk
    org = getattr(user, 'organizer_profile', None)
    return org is not None and team.entries.filter(tournament__organizer=org).exists()


@login_required
def my_teams(request):
    profile = request.user.player_profile
    

    if request.method == 'POST':
        team_id = request.POST.get('team_id')
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        
        if team_id:
            team = get_object_or_404(Team, id=team_id, owner=profile)
            team.name = name
            team.description = description
            team.save()
            messages.success(request, f"Team '{team.name}' updated successfully!")
            return redirect('my_teams')
        
        sport_id = request.POST.get('sport_id')
        sport = get_object_or_404(Sport, id=sport_id)
        if not sport.allow_player_created_teams:
            messages.error(request, "This sport does not allow player-created teams.")
            return redirect('my_teams')
            
        team = Team.objects.create(
            name=name,
            sport=sport,
            description=description,
            owner=profile
        )
        
        TeamMembership.objects.create(
            team=team,
            player=profile,
            role='OWNER',
            roster_status='ACTIVE',
            display_name=request.user.display_name
        )
        
        # Send Email
        from tournaments.tasks import _send
        _send('team_created', f'Your Team "{team.name}" is Ready!', request.user.email, {
            'name': request.user.display_name,
            'team_name': team.name,
            'sport_name': sport.name
        })
        
        messages.success(request, f"Team '{team.name}' created successfully!")
        return redirect('my_teams')

            
        team = Team.objects.create(
            name=name,
            sport=sport,
            description=description,
            owner=profile
        )
        
        TeamMembership.objects.create(
            team=team,
            player=profile,
            role='OWNER',
            roster_status='ACTIVE',
            display_name=request.user.display_name
        )
        
        # Send Email
        from tournaments.tasks import _send
        _send('team_created', f'Your Team "{team.name}" is Ready!', request.user.email, {
            'name': request.user.display_name,
            'team_name': team.name,
            'sport_name': sport.name
        })
        
        messages.success(request, f"Team '{team.name}' created successfully!")
        return redirect('my_teams')

    # GET request
    memberships = TeamMembership.objects.filter(player=profile).select_related('team', 'team__sport')
    teams = [m for m in memberships if not m.team.is_pair]
    
    # Sports for the modal dropdown
    sports = Sport.objects.filter(participation_type='TEAM', allow_player_created_teams=True)
    
    return render(request, 'players/my_teams.html', {
        'memberships': teams,
        'sports': sports
    })

@login_required
def team_manage(request, team_id):
    team = get_object_or_404(Team, id=team_id)
    if not _can_manage_team(team, request.user):
        messages.error(request, "Only the team owner can manage this team.")
        return redirect('content_feed')
        
    if request.method == 'POST':
        if request.POST.get('action') == 'update_logo' and request.FILES.get('logo'):
            logo_file = request.FILES['logo']
            try:
                img = Image.open(logo_file)
                # 1:1 Crop (Center)
                width, height = img.size
                if width != height:
                    min_dim = min(width, height)
                    left = (width - min_dim) / 2
                    top = (height - min_dim) / 2
                    right = (width + min_dim) / 2
                    bottom = (height + min_dim) / 2
                    img = img.crop((left, top, right, bottom))
                
                # Convert to RGB if necessary for WebP saving
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                output = BytesIO()
                img.save(output, format='WEBP', quality=85)
                output.seek(0)
                
                new_filename = f"team_{team.id}_logo.webp"
                team.logo = InMemoryUploadedFile(
                    output, 'ImageField', new_filename, 'image/webp',
                    sys.getsizeof(output), None
                )
                team.save()
                messages.success(request, "Team logo updated successfully!")
            except Exception as e:
                messages.error(request, "Invalid image file.")
            
            return redirect('team_manage', team_id=team.id)
        
    members = team.memberships.all()
    active_count = members.filter(roster_status='ACTIVE').count()
    bench_count = members.filter(roster_status='BENCHED').count()
    from tournaments.models import TeamJoinRequest
    join_requests = TeamJoinRequest.objects.filter(team=team, status='PENDING')
    
    tournaments = [entry.tournament for entry in team.entries.select_related('tournament', 'tournament__sport')]
    return render(request, 'players/team_manage.html', {
        'join_requests': join_requests,
        'team': team,
        'members': members,
        'active_count': active_count,
        'bench_count': bench_count,
        'tournaments': tournaments,
    })

@login_required
def team_member_status(request, team_id, membership_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)
        
    team = get_object_or_404(Team, id=team_id)
    if not _can_manage_team(team, request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    membership = get_object_or_404(TeamMembership, id=membership_id, team=team)
    new_status = request.POST.get('status')
    if new_status in ['ACTIVE', 'BENCHED']:
        # Check sport limits before changing to active
        if new_status == 'ACTIVE' and team.sport.max_active_players:
            current_active = team.memberships.filter(roster_status='ACTIVE').exclude(id=membership.id).count()
            if current_active >= team.sport.max_active_players:
                return JsonResponse({'error': f"Cannot exceed {team.sport.max_active_players} active players."}, status=400)
                
        membership.roster_status = new_status
        membership.save()
        return JsonResponse({'success': True, 'status': new_status})
        
    return JsonResponse({'error': 'Invalid status'}, status=400)


@login_required
def api_search_users(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
        
    # Search by username, display_name or ID
    from django.db.models import Q
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    query = Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
    if q.isdigit():
        query |= Q(id=int(q))
        
    users = User.objects.filter(query).exclude(id=request.user.id).select_related('player_profile')[:10]
    
    results = []
    for u in users:
        avatar_url = u.player_profile.profile_photo.url if hasattr(u, 'player_profile') and u.player_profile.profile_photo else None
        results.append({
            'id': u.id,
            'username': u.username,
            'name': u.display_name,
            'avatar': avatar_url,
            'player_profile_id': u.player_profile.id if hasattr(u, 'player_profile') else None
        })
        
    return JsonResponse({'results': results})

@login_required
def team_add_member(request, team_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)
        
    team = get_object_or_404(Team, id=team_id)
    if not _can_manage_team(team, request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    profile_id = request.POST.get('profile_id')
    if not profile_id:
        return JsonResponse({'error': 'Profile ID is required'}, status=400)
        
    player = get_object_or_404(PlayerProfile, id=profile_id)
    
    # Check if already a member
    if TeamMembership.objects.filter(team=team, player=player).exists():
        return JsonResponse({'error': 'Player is already in the team'}, status=400)
        
    membership = TeamMembership.objects.create(
        team=team,
        player=player,
        display_name=player.user.display_name,
        role='MEMBER',
        roster_status='BENCHED'
    )
    
    from accounts.models import Notification
    Notification.objects.create(
        recipient=player.user,
        verb='joined_team',
        message=f"Congratulations, you are now a part of {team.name}!",
        url=f"/teams/{team.id}"
    )
    
    return JsonResponse({
        'success': True,
        'member': {
            'id': membership.id,
            'display_name': membership.display_name,
            'username': player.user.username,
            'role': membership.role,
            'status': membership.roster_status
        }
    })

@login_required
def team_member_role(request, team_id, membership_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)
        
    team = get_object_or_404(Team, id=team_id)
    if not _can_manage_team(team, request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    membership = get_object_or_404(TeamMembership, id=membership_id, team=team)
    if membership.role == 'OWNER':
        return JsonResponse({'error': 'Cannot change the role of the owner'}, status=400)
        
    new_role = request.POST.get('role')
    if new_role in ['CAPTAIN', 'MEMBER']:
        membership.role = new_role
        membership.save()
        
        if new_role == 'CAPTAIN' and membership.player and membership.player.user:
            from accounts.models import Notification
            Notification.objects.create(
                recipient=membership.player.user,
                verb='promoted',
                message=f"Congratulations, you are now the captain of {team.name}!",
                url=f"/teams/{team.id}"
            )
            
        return JsonResponse({'success': True, 'role': new_role})
        
    return JsonResponse({'error': 'Invalid role'}, status=400)


@login_required
def team_member_remove(request, team_id, membership_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)
        
    team = get_object_or_404(Team, id=team_id)
    if not _can_manage_team(team, request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    membership = get_object_or_404(TeamMembership, id=membership_id, team=team)
    if membership.role == 'OWNER':
        return JsonResponse({'error': 'Cannot remove the owner'}, status=400)
        
    membership.delete()
    return JsonResponse({'success': True})

@login_required
def team_join_request_respond(request, team_id, request_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)
        
    team = get_object_or_404(Team, id=team_id)
    if not _can_manage_team(team, request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    from tournaments.models import TeamJoinRequest
    join_req = get_object_or_404(TeamJoinRequest, id=request_id, team=team, status='PENDING')
    
    action = request.POST.get('action')
    if action not in ['ACCEPT', 'REJECT']:
        return JsonResponse({'error': 'Invalid action'}, status=400)
        
    from accounts.models import Notification
    if action == 'ACCEPT':
        join_req.status = 'ACCEPTED'
        join_req.save()
        
        # Add to team
        TeamMembership.objects.create(
            team=team,
            player=join_req.player,
            display_name=join_req.player.user.display_name,
            role='MEMBER',
            roster_status='BENCHED',
            phone_number=join_req.phone_number
        )
        Notification.push(join_req.player.user, f"You are selected! Welcome to {team.name}.", url=f"/teams/{team.id}")
        
    elif action == 'REJECT':
        join_req.status = 'REJECTED'
        join_req.save()
        Notification.push(join_req.player.user, f"Sorry, you were not selected for {team.name}.", url=f"/teams/{team.id}")
        
    return JsonResponse({'success': True, 'action': action})
