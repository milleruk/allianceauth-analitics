import logging
import requests
import datetime
import csv

from celery import shared_task

from .models import AACharacter, AAzKillMonth
from allianceauth.corputils.models import CorpStats, CorpMember
from allianceauth.eveonline.models import EveCorporationInfo, EveCharacter
from django.utils.dateparse import parse_datetime
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from time import sleep
from math import floor


logger = logging.getLogger(__name__)

@shared_task
def update_character_stats(character_id):
    #logger.info('update_character_stats for %s starting' % str(character_id))
    # https://zkillboard.com/api/stats/characterID/####/
    _stats_request = requests.get("https://zkillboard.com/api/stats/characterID/" + str(character_id) + "/")
    _stats_json = _stats_request.json()
    sleep(1)

    # https://zkillboard.com/api/characterID/####/kills/
    _kills_request = requests.get("https://zkillboard.com/api/characterID/" + str(character_id) + "/kills/")
    _kills_json = _kills_request.json()
    sleep(1)

    _last_kill_date = None
    if len(_kills_json) > 0:
        # https://esi.evetech.net/latest/killmails/ID####/HASH####/?datasource=tranquility
        try:
            _last_kill_request = requests.get(
                "https://esi.evetech.net/latest/killmails/" + str(_kills_json[0]['killmail_id']) + "/" +
                str(_kills_json[0]['zkb']['hash']) + "/?datasource=tranquility")
            _last_kill_json = _last_kill_request.json()
            sleep(1)
            _last_kill_date = parse_datetime(_last_kill_json['killmail_time'])
        except:
            pass

    char_model, created = AACharacter.objects.update_or_create(character = EveCharacter.objects.get(character_id=int(character_id)))
    if created:
        pass
    

    if len(_stats_json.get('months', [])) > 0:
        for key, month in _stats_json.get('months', []).items():
            zkill_month, created = AAzKillMonth.objects.get_or_create(char=char_model, year=month.get('year', 0), month=month.get('month', 0))
            if created:
                pass
            
            zkill_month.ships_destroyed = month.get('shipsDestroyed', 0)
            zkill_month.ships_lost = month.get('shipsLost', 0)
            zkill_month.isk_destroyed = month.get('iskDestroyed', 0)
            zkill_month.isk_lost = month.get('iskLost', 0)
            zkill_month.last_update = datetime.datetime.utcnow().replace(tzinfo=timezone.utc)
            zkill_month.save()

    char_model.isk_destroyed = _stats_json.get('iskDestroyed', 0)
    char_model.isk_lost = _stats_json.get('iskLost', 0)
    char_model.all_time_sum = _stats_json.get('allTimeSum', 0)
    char_model.gang_ratio = _stats_json.get('gangRatio', 0)
    char_model.ships_destroyed = _stats_json.get('shipsDestroyed', 0)
    char_model.ships_lost = _stats_json.get('shipsLost', 0)
    char_model.solo_destroyed = _stats_json.get('soloDestroyed', 0)
    char_model.solo_lost = _stats_json.get('soloLost', 0)
    char_model.active_pvp_kills = _stats_json.get('activepvp', {}).get('kills', {}).get('count', 0)
    char_model.last_kill = _last_kill_date
    char_model.last_update = datetime.datetime.utcnow().replace(tzinfo=timezone.utc)
    char_model.save()

    #logger.info('update_character_stats for %s complete' % str(character_id))
def update_char(char_name, char_id):
    try:
        logger.info('update_character_stats for %s starting' % str(char_name))
        update_character_stats(char_id)
    except:
        logger.error('update_character_stats failed for %s' % str(char_name))
        logging.exception("Messsage")
        sleep(1)  # had an error printed it and skipped it YOLO. better wait a sec to not overload the api
        pass


@shared_task(name='authanaliticis.tasks.run_stat_model_update')
def run_stat_model_update():
    # update all corpstat'd characters
    #logger.info('start')
    active_corp_stats = CorpStats.objects.all()
    member_alliances = ['499005583', '1900696668'] # hardcoded cause *YOLO*
    stale_date = datetime.datetime.utcnow().replace(tzinfo=timezone.utc) - datetime.timedelta(hours=168)
    for cs in active_corp_stats:
        members = cs.mains
        for member in members:
            for alt in member.alts:
                if alt.alliance_id in member_alliances:
                    try:
                        if AACharacter.objects.get(character__character_id=alt.character_id).last_update<stale_date:
                            update_char(alt.character_name, alt.character_id)
                    except ObjectDoesNotExist:
                        update_char(alt.character_name, alt.character_id)

        #missing = cs.unregistered_members
        #for member in missing:
            #update_character_stats.delay(member.character_id)

def output_stats(file_output=True):
    active_corp_stats = CorpStats.objects.all()
    #member_alliances = ['499005583', '1900696668'] # hardcoded cause *YOLO*
    #for cs in active_corp_stats:
        #members = cs.mains
        #for member in members:
            #update_character_stats(member.character_id)
            #for alt in member.alts:
                #if alt.alliance_id in member_alliances:
                    #if alt.character_name != member.character_name:
                        #update_character_stats(alt.character_id)
        #missing = cs.unregistered_members
        #for member in missing:
        #    update_character_stats(member.character_id)
    out_arr={}
    for cs in active_corp_stats:
        members = cs.mains
        for member in members:
            print("Adding: %s" % member.character.character_name)
            now = datetime.datetime.now()
            #try:
            in_char = EveCharacter.objects.get(
                character_id=member.character.character_id).character_ownership.user.profile.main_character
            character_list = in_char.character_ownership.user.character_ownerships.all().select_related('character')
            character_ids = set(character_list.values_list('character__character_id', flat=True))

            month_12_ago = ((now.month - 1 - 12) % 12 + 1)
            month_6_ago = ((now.month - 1 - 6) % 12 + 1)
            month_3_ago = ((now.month - 1 - 3) % 12 + 1)
            year_12_ago = (now.year + floor((now.month - 12) / 12))
            year_6_ago = (now.year + floor((now.month - 6) / 12))
            year_3_ago = (now.year + floor((now.month - 3) / 12))

            character = AACharacter.objects.filter(character__character_id__in=character_ids)

            qs = AAzKillMonth.objects.filter(char__in=character)

            qs_12m = qs.filter(year=year_12_ago, month__gte=month_12_ago) | \
                     qs.filter(year=now.year)
            qs_12m = qs_12m.aggregate(ship_destroyed_sum=Coalesce(Sum('ships_destroyed'), 0)).get(
                'ship_destroyed_sum', 0)

            qs_6m = qs.filter(year=year_6_ago, month__gte=month_6_ago)
            if now.month < 6:
                qs_6m = qs_6m | qs.filter(year=now.year)
            qs_6m = qs_6m.aggregate(ship_destroyed_sum=Coalesce(Sum('ships_destroyed'), 0)).get(
                'ship_destroyed_sum', 0)

            qs_3m = qs.filter(year__gte=year_3_ago, month__gte=month_3_ago)
            if now.month < 3:
                qs_3m = qs_3m | qs.filter(year=now.year)
            qs_3m = qs_3m.aggregate(ship_destroyed_sum=Coalesce(Sum('ships_destroyed'), 0)).get(
                'ship_destroyed_sum', 0)

            out_str=[]
            out_str.append(in_char.character_name)
            out_str.append(in_char.corporation_name)
            out_str.append(str(qs_12m))
            out_str.append(str(qs_6m))
            out_str.append(str(qs_3m))
            out_arr[in_char.character_name]=out_str
            #except:
             #   pass
    
    if file_output:
        with open('auth_zkill_dump.csv', 'w') as writeFile:
            writer = csv.writer(writeFile)
            writer.writerow(['Name', 'Corp', '12m', '6m', '3m'])
            writer.writerows(out_arr)
        writeFile.close()
    else:
        return out_arr
    
