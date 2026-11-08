from jarvis.application.routines import digest
from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle


def test_routines_widgets_edit_order_preview_delete_without_execution(tmp_path):
    app,h,service,controller,window=window_system(tmp_path)
    calls=[]
    original=h._execute_tool
    h._execute_tool=lambda *args,**kwargs: calls.append(args)
    try:
        window.nav_buttons['Rotinas'].click()
        page=window.routines_page
        settle(app,lambda:bool(page.data) and not page.busy)
        assert page.list.count()==3
        page.list.setCurrentRow(1)
        page.choice.setCurrentIndex(0)
        page.add();page.add();page.steps.setCurrentRow(0);page.move(1)
        assert len(page.definition['steps'])==2
        page.enabled.setChecked(True);page.save()
        settle(app,lambda:not page.busy and any(r['enabled'] for r in page.data['rows']))
        page.list.setCurrentRow(next(i for i,r in enumerate(page.data['rows']) if r['id']=='study'))
        page.preview();settle(app,lambda:not page.busy)
        assert not calls
        page.name.setText('Outro nome');assert page.token is None
        page.delete();settle(app,lambda:not page.busy and len(page.data['rows'])==2)
        assert not calls
    finally:
        h._execute_tool=original
        close_system(h,controller,window)
