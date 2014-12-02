import json
from django.contrib import messages
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from smartmin.views import SmartCRUDL, SmartListView, SmartReadView, SmartUpdateView, SmartFormView
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.orgs.views import OrgPermsMixin
from temba.utils import build_json_response


class BoundaryCRUDL(SmartCRUDL):
    actions = ('list', 'alias', 'geometry', 'boundaries')
    model = AdminBoundary

    class List(OrgPermsMixin, SmartListView):
        link_fields = ('name',)
        search_fields = ('name__icontains', 'osm_id__icontains')

        def get_geometry(self, obj):
            return obj.geometry.num_coords

        def get_simplified_geometry(self, obj):
            return obj.simplified_geometry.num_coords

    class Alias(OrgPermsMixin, SmartReadView):

        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed in, that is derived
            return r'^%s/%s/$' % (path, action)

        def pre_process(self, request, *args, **kwargs):
            response = super(BoundaryCRUDL.Alias, self).pre_process(self, request, *args, **kwargs)

            # we didn't shortcut for some other reason, check that they have an org
            if not response:
                org = request.user.get_org()
                if not org.country:
                    messages.add_message(request, 'alert', _("You must select a country for your organization."))
                    return HttpResponseRedirect(reverse('orgs.org_home'))

            return None

        def get_object(self, queryset=None):
            org = self.request.user.get_org()
            return org.country

    class Geometry(OrgPermsMixin, SmartReadView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed in, that is derived
            return r'^%s/%s/(?P<osmId>\w\d+)/$' % (path, action)

        def get_object(self):
            return AdminBoundary.objects.get(osm_id=self.kwargs['osmId'])

        def render_to_response(self, context):
            if self.object.children.all().count() > 0:
                return HttpResponse(self.object.get_children_geojson(), content_type='application/json')
            return HttpResponse(self.object.get_geojson(), content_type='application/json')

    class Boundaries(OrgPermsMixin, SmartUpdateView):

        @csrf_exempt
        def dispatch(self, *args, **kwargs):
            return super(BoundaryCRUDL.Boundaries, self).dispatch(*args, **kwargs)

        @classmethod
        def derive_url_pattern(cls, path, action):
            # though we are a read view, we don't actually need an id passed in, that is derived
            return r'^%s/%s/(?P<osmId>\w\d+)/$' % (path, action)

        def get_object(self):
            return AdminBoundary.objects.get(osm_id=self.kwargs['osmId'])

        def post(self, request, *args, **kwargs):

            def update_aliases(boundary, new_aliases):
                # for now, nuke and recreate all aliases
                BoundaryAlias.objects.filter(boundary=boundary, org=org).delete()
                for new_alias in new_aliases.split('\n'):
                    if new_alias:
                        BoundaryAlias.objects.create(boundary=boundary, org=org, name=new_alias,
                                                     created_by=self.request.user, modified_by=self.request.user)

            # try to parse our body
            json_string = request.body
            org = request.user.get_org()

            try:
                json_dict = json.loads(json_string)
            except Exception as e:
                return build_json_response(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            # this can definitely be optimized
            for state in json_dict:
                state_boundary = AdminBoundary.objects.filter(osm_id=state['osm_id']).first()
                state_aliases = state.get('aliases', '')
                if state_boundary:
                    update_aliases(state_boundary, state_aliases)
                    if 'children' in state:
                        for district in state['children']:
                            district_boundary = AdminBoundary.objects.filter(osm_id=district['osm_id']).first()
                            district_aliases = district.get('aliases', '')
                            update_aliases(district_boundary, district_aliases)

            return build_json_response(json_dict)

        def get(self, request, *args, **kwargs):
            tops = list(AdminBoundary.objects.filter(parent__osm_id=self.get_object().osm_id).order_by('name'))
            children = AdminBoundary.objects.filter(Q(parent__osm_id__in=[boundary.osm_id for boundary in tops])).order_by('parent__osm_id', 'name')

            boundaries = []
            for top in tops:
                boundaries.append(top.as_json())

            current_top = None
            match = ''
            for child in children:
                child = child.as_json()

                # find the appropriate top if necessary
                if not current_top or current_top['osm_id'] != child['parent_osm_id']:
                    for top in boundaries:
                        if top['osm_id'] == child['parent_osm_id']:
                            current_top = top
                            match = '%s %s' % (current_top['name'], current_top['aliases'])

                children = current_top.get('children', [])
                child['match'] = '%s %s %s %s' % (child['name'], child['aliases'], current_top['name'], current_top['aliases'])
                children.append(child)
                match = '%s %s %s' % (match, child['name'], child['aliases'])
                current_top['children'] = children
                current_top['match'] = match

            return build_json_response(boundaries)
